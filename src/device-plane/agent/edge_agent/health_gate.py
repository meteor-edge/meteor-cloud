"""Wait for an updated workload to become healthy (a release's ``health_check``).

After an update, the agent watches the workload for up to ``timeout_seconds``. It counts
as healthy once the runtime says so (unit active, container running and its HEALTHCHECK
passing) and the optional ``http_url`` answers 2xx or 3xx, and only after it stayed that
way for ``stable_seconds``. If not, the caller rolls back to the previous release.
See docs/fleet/rollback.md.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("edge_agent")

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_STABLE_SECONDS = 10
POLL_SECONDS = 2.0
_PROBE_TIMEOUT_SECONDS = 5.0

# (healthy, problem): healthy is True, False, or None while it is still starting.
RuntimeCheck = Callable[[], tuple[bool | None, str | None]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_probe_opener = urllib.request.build_opener(_NoRedirect)


def http_probe(url: str) -> str | None:
    """None when ``url`` answers 2xx or 3xx, else why not."""
    try:
        with _probe_opener.open(urllib.request.Request(url, method="GET"), timeout=_PROBE_TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return f"{url} did not answer ({reason})"
    if 200 <= status < 400:
        return None
    return f"{url} answered HTTP {status}"


def gate_settings(spec: dict[str, Any]) -> dict[str, Any] | None:
    """The release's health_check with defaults filled in, or None when it has none."""
    raw = spec.get("health_check")
    if not isinstance(raw, dict):
        return None
    stable = raw.get("stable_seconds")
    return {
        "timeout_seconds": int(raw.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
        "stable_seconds": int(stable if stable is not None else DEFAULT_STABLE_SECONDS),
        "http_url": raw.get("http_url") or None,
        "rollback": raw.get("rollback", True) is not False,
    }


def wait_until_healthy(
    check: RuntimeCheck,
    *,
    timeout_seconds: int,
    stable_seconds: int,
    http_url: str | None = None,
    probe: Callable[[str], str | None] = http_probe,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """None once the workload stayed healthy for ``stable_seconds``, else the last problem.

    It has ``timeout_seconds`` to become healthy; a workload that turns unhealthy again
    starts the stable window over.
    """
    started = clock()
    deadline = started + timeout_seconds + stable_seconds
    healthy_since: float | None = None
    problem: str | None = None
    while True:
        now = clock()
        healthy, runtime_problem = check()
        if healthy is True and http_url:
            runtime_problem = probe(http_url)
            healthy = runtime_problem is None
        if healthy is True:
            healthy_since = now if healthy_since is None else healthy_since
            if now - healthy_since >= stable_seconds:
                return None
        else:
            healthy_since = None
            problem = runtime_problem or problem
            if now - started >= timeout_seconds:
                return problem or f"not healthy within {timeout_seconds}s"
        if now >= deadline:
            return problem or f"not healthy within {timeout_seconds}s"
        sleep(POLL_SECONDS)
