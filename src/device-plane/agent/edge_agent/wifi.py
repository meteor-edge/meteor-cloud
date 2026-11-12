"""Wi-Fi changes ordered from the console.

The heartbeat response says when a change is waiting. The agent then fetches it
(the only moment a password travels), hands it to the root-owned
``meteorcli-wifi`` helper on stdin, and reports what happened. A change marked
"connect now" is kept only if MeteorCloud can still be reached afterwards;
otherwise the helper puts the old settings back. Passwords are never logged or
written by the agent itself.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any

from edge_agent.client import AgentApiError, EdgeClient

logger = logging.getLogger("edge_agent")

# Root-owned helper installed by installcli.sh; runs via `sudo -n` for a non-root agent.
WIFI_HELPER = "/usr/local/libexec/meteorcli-wifi"
HELPER_TIMEOUT_SECONDS = 150
# Helper exit codes (see packaging/meteorcli-wifi).
EXIT_NOT_JOINED = 3

# After switching networks: how long MeteorCloud may take to answer before going back.
VERIFY_SECONDS = 90.0
VERIFY_PAUSE_SECONDS = 5.0
# Wi-Fi state is reported when it changes, and at least this often.
STATUS_REFRESH_SECONDS = 300.0

_NOT_INSTALLED = "The Wi-Fi helper is not installed on this device. Run the meteorcli installer with sudo."
_UNREACHABLE = (
    "The device joined the network but could not reach MeteorCloud through it. The old Wi-Fi settings were put back."
)
_INTERRUPTED = "The device restarted before the new network was confirmed. The old Wi-Fi settings were put back."

Runner = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def helper_command(*args: str) -> list[str]:
    """``meteorcli-wifi`` argv, through sudo unless already root."""
    base = [WIFI_HELPER] if os.geteuid() == 0 else ["sudo", "-n", WIFI_HELPER]
    return [*base, *args]


def helper_installed() -> bool:
    return os.path.isfile(WIFI_HELPER)


def _default_runner(args: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), input=stdin or "", capture_output=True, text=True, timeout=HELPER_TIMEOUT_SECONDS, check=False
    )


def _result(change_id: Any, revision: Any, state: str, error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"id": change_id, "revision": revision, "state": state}
    if error:
        result["error"] = error[:500]
    return result


class WifiSync:
    """Applies waiting Wi-Fi changes and keeps the console's view of this device's Wi-Fi current."""

    def __init__(
        self,
        client: EdgeClient,
        device_token: str,
        *,
        runner: Runner = _default_runner,
        installed: Callable[[], bool] = helper_installed,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.device_token = device_token
        self._runner = runner
        self._installed = installed
        self._sleep = sleep
        self._clock = clock
        self._started = False
        self._unreported: list[dict[str, Any]] = []
        self._last_status: dict[str, Any] | None = None
        self._last_status_at: float | None = None

    # ------------------------------------------------------------- helper
    def _helper(self, command: str, request: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        """Run the helper; (exit code, its JSON answer). Never raises, never logs the request."""
        if not self._installed():
            return 127, {"error": _NOT_INSTALLED}
        try:
            done = self._runner(helper_command(command), json.dumps(request) if request is not None else None)
        except (OSError, subprocess.SubprocessError) as exc:
            return 126, {"error": f"The Wi-Fi helper could not run ({type(exc).__name__})."}
        try:
            answer = json.loads(done.stdout or "{}")
        except ValueError:
            answer = {}
        if not isinstance(answer, dict):
            answer = {}
        if done.returncode != 0 and "error" not in answer:
            # sudo refusing (no rule for this user) lands here; stderr has no secrets, stdin is not echoed.
            answer["error"] = (done.stderr or "").strip()[:300] or "The Wi-Fi helper failed."
        return done.returncode, answer

    def status(self) -> dict[str, Any]:
        """This device's Wi-Fi, in the shape the server expects (no passwords)."""
        code, answer = self._helper("status")
        if code != 0:
            return {"supported": False, "unsupported_reason": str(answer.get("error", ""))[:255]}
        keys = (
            "supported",
            "unsupported_reason",
            "connected_ssid",
            "signal_percent",
            "interface",
            "wired_connected",
            "saved_networks",
        )
        return {key: answer[key] for key in keys if key in answer}

    # -------------------------------------------------------------- steps
    def _recover(self) -> None:
        """A change left unconfirmed by a restart or power cut: go back to the old settings."""
        code, answer = self._helper("status")
        pending = answer.get("pending") if code == 0 else None
        if not isinstance(pending, dict):
            return
        logger.warning("An unconfirmed Wi-Fi change was found; putting the old settings back.")
        self._helper("rollback")
        if pending.get("id") and isinstance(pending.get("revision"), int):
            self._unreported.append(_result(pending["id"], pending["revision"], "rolled_back", _INTERRUPTED))

    def _server_reachable(self) -> bool:
        deadline = self._clock() + VERIFY_SECONDS
        while True:
            try:
                self.client.health()
                return True
            except Exception:  # noqa: BLE001 - any failure means "not reachable yet"
                pass
            if self._clock() >= deadline:
                return False
            self._sleep(VERIFY_PAUSE_SECONDS)

    def _apply(self, change: dict[str, Any]) -> dict[str, Any]:
        change_id, revision = change.get("id"), change.get("revision")
        if change.get("action") == "remove":
            code, answer = self._helper("remove", {"ssid": change.get("ssid")})
            if code == 0:
                logger.info("Wi-Fi network removed.")
                return _result(change_id, revision, "removed")
            return _result(change_id, revision, "failed", str(answer.get("error", "")))

        connect_now = change.get("connect_now") is True
        request = {
            "id": change_id,
            "revision": revision,
            "ssid": change.get("ssid"),
            "security": change.get("security", "wpa-psk"),
            "password": change.get("password"),
            "hidden": change.get("hidden") is True,
            "connect_now": connect_now,
        }
        code, answer = self._helper("apply", request)
        if code == EXIT_NOT_JOINED:
            logger.warning("Wi-Fi change not kept: the network could not be joined.")
            return _result(change_id, revision, "rolled_back", str(answer.get("error", "")))
        if code != 0:
            return _result(change_id, revision, "failed", str(answer.get("error", "")))
        if not connect_now:
            logger.info("Wi-Fi network saved for the next connection.")
            return _result(change_id, revision, "applied")
        if self._server_reachable():
            self._helper("confirm")
            logger.info("Wi-Fi changed; MeteorCloud is reachable on the new network.")
            return _result(change_id, revision, "applied")
        logger.warning("Wi-Fi change not kept: MeteorCloud was unreachable on the new network.")
        self._helper("rollback")
        return _result(change_id, revision, "rolled_back", _UNREACHABLE)

    def _fetch_and_apply(self) -> None:
        changes = self.client.get_wifi_changes(device_token=self.device_token)
        for change in changes:
            if isinstance(change, dict):
                self._unreported.append(self._apply(change))

    def _report(self, *, force_status: bool) -> None:
        status = self.status()
        now = self._clock()
        stale = self._last_status_at is None or now - self._last_status_at >= STATUS_REFRESH_SECONDS
        send_status = force_status or stale or status != self._last_status
        if not send_status and not self._unreported:
            return
        body: dict[str, Any] = {"results": list(self._unreported)}
        if send_status:
            body["status"] = status
        self.client.report_wifi(device_token=self.device_token, payload=body)
        self._unreported.clear()
        if send_status:
            self._last_status, self._last_status_at = status, now

    # --------------------------------------------------------------- entry
    def on_heartbeat(self, response: dict[str, Any]) -> None:
        """Called with each heartbeat response. Never raises: Wi-Fi must not stop heartbeats."""
        try:
            changed = False
            if not self._started:
                self._started = True
                self._recover()
            if response.get("wifi_pending") is True:
                self._fetch_and_apply()
                changed = True
            self._report(force_status=changed)
        except AgentApiError as error:
            if error.status == 404:
                return  # a server from before Wi-Fi management
            logger.warning("Wi-Fi sync failed (%s); trying again with the next heartbeat.", error.code)
        except Exception:
            logger.exception("Wi-Fi sync failed; trying again with the next heartbeat.")
