"""Device log sources (journal, systemd units, Docker containers, /var/log files).

The root-owned ``meteorcli-logs`` helper (installed by installcli.sh) does the
listing and printing; the agent only reports what it lists on heartbeat so the
console can offer those sources. Without the helper nothing is reported.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger("edge_agent")

# Root-owned helper installed by installcli.sh; runs via `sudo -n` for a non-root agent.
LOGS_HELPER = "/usr/local/libexec/meteorcli-logs"
# Sources change rarely (a deploy, a new container): list them every few minutes, not every heartbeat.
REFRESH_SECONDS = 300.0
SOURCE_KINDS = frozenset({"journal", "systemd", "docker", "file"})
MAX_SOURCES = 300

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def helper_command(*args: str) -> list[str]:
    """``meteorcli-logs`` argv, through sudo unless already root."""
    base = [LOGS_HELPER] if os.geteuid() == 0 else ["sudo", "-n", LOGS_HELPER]
    return [*base, *args]


def helper_installed() -> bool:
    return os.path.isfile(LOGS_HELPER)


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=30, check=False)


def _clean(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("kind") not in SOURCE_KINDS:
        return None
    name = item.get("name")
    if not isinstance(name, str) or len(name) > 255:
        return None
    out: dict[str, Any] = {"kind": item["kind"], "name": name}
    for key in ("state", "project"):
        value = item.get(key)
        if isinstance(value, str) and value:
            out[key] = value[:64]
    if isinstance(item.get("size"), int):
        out["size"] = item["size"]
    return out


def list_log_sources(*, runner: Runner = _default_runner) -> list[dict[str, Any]] | None:
    """Sources the helper lists, or None when it is missing or fails."""
    if not helper_installed():
        return None
    try:
        result = runner(helper_command("list"))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("meteorcli-logs list failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.debug("meteorcli-logs list failed: %s", (result.stderr or "").strip()[:200])
        return None
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list):
        return None
    return [item for item in map(_clean, raw[:MAX_SOURCES]) if item is not None]


class LogSourceCache:
    """Re-list log sources at most every ``REFRESH_SECONDS``."""

    def __init__(
        self,
        *,
        lister: Callable[[], list[dict[str, Any]] | None] = list_log_sources,
        clock: Callable[[], float] = time.monotonic,
        refresh_seconds: float = REFRESH_SECONDS,
    ) -> None:
        self._lister = lister
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._sources: list[dict[str, Any]] | None = None
        self._listed_at: float | None = None

    def get(self) -> list[dict[str, Any]] | None:
        now = self._clock()
        if self._listed_at is None or now - self._listed_at >= self._refresh_seconds:
            self._sources = self._lister()
            self._listed_at = now
        return self._sources


def add_log_sources(payload: dict[str, Any], cache: LogSourceCache) -> dict[str, Any]:
    """Add ``log_sources`` to a heartbeat payload when the helper lists any."""
    sources = cache.get()
    if sources is not None:
        payload["log_sources"] = sources
    return payload
