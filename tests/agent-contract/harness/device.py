"""A simulated device: what the host would do when the agent asks it to do something.

The contract is the conversation with the control plane, not what happens on the
host. Agent adapters route privileged work (docker, Wi-Fi, WireGuard, reboot ...) to
this object instead of the real machine, and scenarios assert on what it recorded.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

APP = "demo-app"
LOADED_IMAGE = "demo/app:1.0"


def _done(args: Sequence[str], code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(args), code, stdout=stdout, stderr=stderr)


@dataclass
class SimulatedDevice:
    host_actions: list[tuple[Any, ...]] = field(default_factory=list)
    docker_commands: list[list[str]] = field(default_factory=list)
    containers: dict[str, str] = field(default_factory=dict)  # name -> image
    wifi_requests: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)
    wifi_ssid: str | None = "office"
    wifi_saved: list[str] = field(default_factory=lambda: ["office"])
    wg_commands: list[list[str]] = field(default_factory=list)
    journal: list[dict[str, Any]] = field(default_factory=list)
    _app_looks: int = 0

    # ------------------------------------------------------------- reboot, users
    def reboot(self) -> None:
        self.host_actions.append(("reboot",))

    def shutdown(self) -> None:
        self.host_actions.append(("shutdown",))

    def set_password(self, username: str, password: str) -> None:
        self.host_actions.append(("set_password", username, password))

    def add_user(self, username: str, password: str, *, sudo: bool = False, authorized_key: str | None = None) -> None:
        self.host_actions.append(("add_user", username, password, sudo, authorized_key))

    # -------------------------------------------------------------------- docker
    def docker(self, args: Sequence[str], check: bool = False, **_: Any) -> subprocess.CompletedProcess[str]:
        args = list(args)
        self.docker_commands.append(args)
        verb = args[1] if len(args) > 1 else ""
        if verb == "load":
            return _done(args, stdout=f"Loaded image: {LOADED_IMAGE}\n")
        if verb == "rm":
            self.containers.pop(args[-1], None)
            return _done(args)
        if verb == "run":
            name = args[args.index("--name") + 1]
            self.containers[name] = args[-1]
            return _done(args, stdout="0123456789ab\n")
        if verb == "ps":
            # The observer's first look is its baseline; the app "starts" right after it.
            self._app_looks += 1
            names = list(self.containers) + ([APP] if self._app_looks > 1 else [])
            return _done(args, stdout="\n".join(dict.fromkeys(names)) + "\n")
        if verb == "inspect" and not args[3].startswith("{{.Name}}"):  # runtime health check
            return _done(args, stdout="true|running|0|\n") if args[-1] in self.containers else _done(args, 1)
        if verb == "inspect":  # observer: name, state, restarts of each container
            lines = [f"/{name}|running|true|0|false|0|2026-01-01T00:00:00Z|" for name in args[4:]]
            return _done(args, stdout="\n".join(lines) + "\n")
        if verb == "stats":
            return _done(args, stdout=f"{APP}|1.50%|10MiB / 100MiB\n")
        return _done(args, 1, stderr=f"unexpected docker call {args}")

    def journal_helper(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        lines, self.journal = self.journal, []
        return _done(args, stdout="".join(json.dumps(line) + "\n" for line in lines))

    # --------------------------------------------------------------------- Wi-Fi
    def wifi_helper(self, args: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        command = list(args)[-1]
        request = json.loads(stdin) if stdin else None
        self.wifi_requests.append((command, request))
        if command == "status":
            return _done(args, stdout=json.dumps({
                "supported": True,
                "connected_ssid": self.wifi_ssid,
                "signal_percent": 72,
                "interface": "wlan0",
                "wired_connected": False,
                "saved_networks": [{"ssid": ssid, "managed": ssid != "office"} for ssid in self.wifi_saved],
            }))
        if command == "apply" and request:
            if request["ssid"] not in self.wifi_saved:
                self.wifi_saved.append(request["ssid"])
            if request.get("connect_now"):
                self.wifi_ssid = request["ssid"]
            return _done(args, stdout="{}")
        if command == "remove" and request:
            self.wifi_saved = [s for s in self.wifi_saved if s != request["ssid"]]
            return _done(args, stdout="{}")
        return _done(args, stdout="{}")  # confirm, rollback

    # ----------------------------------------------------------------- WireGuard
    def wireguard(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.wg_commands.append(list(args))
        return _done(args)
