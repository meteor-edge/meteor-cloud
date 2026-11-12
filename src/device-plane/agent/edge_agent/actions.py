"""One-off actions ordered from the console: restart, shut down, SSH users.

The heartbeat response says when an action is waiting. The agent then fetches it
(the only moment a password travels), hands it to the root-owned
``meteorcli-action`` helper, and reports what happened. Each action is collected
once: a restart is never run again after the device comes back. Passwords are
never logged or written by the agent itself.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from edge_agent.client import AgentApiError, EdgeClient

logger = logging.getLogger("edge_agent")

ACTION_HELPER = "/usr/local/libexec/meteorcli-action"
HELPER_TIMEOUT_SECONDS = 60
_NOT_INSTALLED = "The device-action helper is not installed on this device. Run the meteorcli installer with sudo."

Runner = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def helper_installed() -> bool:
    return os.path.isfile(ACTION_HELPER) and os.access(ACTION_HELPER, os.X_OK)


def helper_command(*args: str) -> list[str]:
    base = [ACTION_HELPER] if os.geteuid() == 0 else ["sudo", "-n", ACTION_HELPER]
    return [*base, *args]


def _default_runner(argv: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=HELPER_TIMEOUT_SECONDS,
        check=False,
    )


def _result(action_id: object, state: str, error: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"id": action_id, "state": state}
    if error:
        row["error"] = error[:500]
    return row


class ActionSync:
    """Applies waiting restart, shut-down and SSH-user actions."""

    def __init__(
        self,
        client: EdgeClient,
        device_token: str,
        *,
        runner: Runner = _default_runner,
        installed: Callable[[], bool] = helper_installed,
    ) -> None:
        self.client = client
        self.device_token = device_token
        self._runner = runner
        self._installed = installed
        self._unreported: list[dict[str, Any]] = []

    def _helper(self, command: str, request: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        if not self._installed():
            return 127, {"error": _NOT_INSTALLED}
        try:
            done = self._runner(helper_command(command), json.dumps(request) if request is not None else None)
        except (OSError, subprocess.SubprocessError) as exc:
            return 126, {"error": f"The action helper could not run ({type(exc).__name__})."}
        try:
            answer = json.loads(done.stdout or "{}")
        except ValueError:
            answer = {}
        if not isinstance(answer, dict):
            answer = {}
        if done.returncode != 0 and "error" not in answer:
            answer["error"] = (done.stderr or "").strip()[:300] or "The action helper failed."
        return done.returncode, answer

    def _apply(self, action: dict[str, Any]) -> dict[str, Any]:
        action_id = action.get("id")
        kind = action.get("kind")
        if kind == "reboot":
            code, answer = self._helper("reboot")
            if code == 0:
                logger.info("Restart ordered.")
                return _result(action_id, "done")
            return _result(action_id, "failed", str(answer.get("error", "")))
        if kind == "shutdown":
            code, answer = self._helper("shutdown")
            if code == 0:
                logger.info("Shut-down ordered.")
                return _result(action_id, "done")
            return _result(action_id, "failed", str(answer.get("error", "")))
        if kind == "set_password":
            code, answer = self._helper(
                "set-password",
                {"username": action.get("username"), "password": action.get("password")},
            )
            if code == 0:
                logger.info("Password changed for a user.")
                return _result(action_id, "done")
            return _result(action_id, "failed", str(answer.get("error", "")))
        if kind == "add_user":
            code, answer = self._helper(
                "add-user",
                {
                    "username": action.get("username"),
                    "password": action.get("password"),
                    "sudo": action.get("sudo") is True,
                    "authorized_key": action.get("authorized_key"),
                },
            )
            if code == 0:
                logger.info("SSH user added.")
                return _result(action_id, "done")
            return _result(action_id, "failed", str(answer.get("error", "")))
        return _result(action_id, "failed", f"Unknown action kind: {kind!r}")

    def _report(self, results: list[dict[str, Any]]) -> None:
        if not results:
            return
        # Copy: callers may clear the list they passed after this returns.
        self.client.report_actions(
            device_token=self.device_token, payload={"results": list(results)}
        )

    def _fetch_and_apply(self) -> None:
        actions = self.client.get_actions(device_token=self.device_token)
        for action in actions:
            if not isinstance(action, dict):
                continue
            kind = action.get("kind")
            # Restart and shut-down: tell the server first, then start them, so the
            # answer is not lost when the process is killed.
            if kind in {"reboot", "shutdown"}:
                self._report([_result(action.get("id"), "done")])
                result = self._apply(action)
                if result.get("state") == "failed":
                    self._report([result])
                continue
            self._unreported.append(self._apply(action))

    def on_heartbeat(self, response: dict[str, Any]) -> None:
        """Called with each heartbeat response. Never raises: actions must not stop heartbeats."""
        try:
            if response.get("actions_pending") is True:
                self._fetch_and_apply()
                self._report(self._unreported)
                self._unreported.clear()
        except AgentApiError as error:
            if error.status == 404:
                return
            logger.warning("Device-action sync failed (%s); trying again with the next heartbeat.", error.code)
        except Exception:
            logger.exception("Device-action sync failed; trying again with the next heartbeat.")
