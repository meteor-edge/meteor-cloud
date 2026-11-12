"""One-off actions ordered from the console: restart, shut down, SSH users, agent update.

The heartbeat response says when an action is waiting. The agent then fetches it
(the only moment a password travels), hands it to a :class:`~edge_agent.platform.ports.HostActions`
adapter (Linux default: root-owned ``meteorcli-action``), and reports what happened.
Each action is collected once: a restart is never run again after the device comes
back. Passwords are never logged or written by the agent itself.

``update_agent`` starts the safe meteorcli updater in a separate process and reports
done or failed on a later heartbeat once that process has finished.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from edge_agent.agent_update import settle_pending, start_updater
from edge_agent.client import AgentApiError, EdgeClient
from edge_agent.platform.linux import (
    ACTION_HELPER,
    SystemdHostActions,
    helper_command,
    helper_installed,
)
from edge_agent.platform.ports import HostActionError, HostActions

logger = logging.getLogger("edge_agent")

# Re-export for packaging tests and older imports.
__all__ = [
    "ACTION_HELPER",
    "ActionSync",
    "helper_command",
    "helper_installed",
]


def _result(action_id: object, state: str, error: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"id": action_id, "state": state}
    if error:
        row["error"] = error[:500]
    return row


def _default_config_dir() -> Path:
    env = os.environ.get("METEORCLI_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    if os.geteuid() == 0:
        return Path("/etc/meteorcli")
    return Path.home() / ".config" / "meteorcli"


class ActionSync:
    """Applies waiting restart, shut-down, SSH-user and agent-update actions."""

    def __init__(
        self,
        client: EdgeClient,
        device_token: str,
        *,
        host: HostActions | None = None,
        runner: Callable[[Sequence[str], str | None], Any] | None = None,
        installed: Callable[[], bool] | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self.client = client
        self.device_token = device_token
        self.config_dir = Path(config_dir) if config_dir is not None else _default_config_dir()
        if host is not None:
            self._host = host
        else:
            kwargs: dict[str, Any] = {}
            if runner is not None:
                kwargs["runner"] = runner
            if installed is not None:
                kwargs["installed"] = installed
            self._host = SystemdHostActions(**kwargs)
        self._unreported: list[dict[str, Any]] = []

    def _apply(self, action: dict[str, Any]) -> dict[str, Any] | None:
        """Apply one action. Returns a report row, or None when a later heartbeat will report."""
        action_id = action.get("id")
        kind = action.get("kind")
        try:
            if kind == "reboot":
                self._host.reboot()
                logger.info("Restart ordered.")
                return _result(action_id, "done")
            if kind == "shutdown":
                self._host.shutdown()
                logger.info("Shut-down ordered.")
                return _result(action_id, "done")
            if kind == "set_password":
                self._host.set_password(str(action.get("username") or ""), str(action.get("password") or ""))
                logger.info("Password changed for a user.")
                return _result(action_id, "done")
            if kind == "add_user":
                self._host.add_user(
                    str(action.get("username") or ""),
                    str(action.get("password") or ""),
                    sudo=action.get("sudo") is True,
                    authorized_key=action.get("authorized_key"),
                )
                logger.info("SSH user added.")
                return _result(action_id, "done")
            if kind == "update_agent":
                error = start_updater(
                    config_dir=self.config_dir,
                    action_id=action_id,
                    from_version=action.get("from_version"),
                )
                if error:
                    return _result(action_id, "failed", error)
                # Report after the updater finishes (next heartbeats).
                return None
        except HostActionError as error:
            return _result(action_id, "failed", str(error))
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
                if result is not None and result.get("state") == "failed":
                    self._report([result])
                continue
            result = self._apply(action)
            if result is not None:
                self._unreported.append(result)

    def _report_settled_update(self) -> None:
        settled = settle_pending(self.config_dir)
        if settled is not None:
            self._report([settled])

    def on_heartbeat(self, response: dict[str, Any]) -> None:
        """Called with each heartbeat response. Never raises: actions must not stop heartbeats."""
        try:
            self._report_settled_update()
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
