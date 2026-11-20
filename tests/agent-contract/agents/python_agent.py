"""Adapter that drives the Python meteorcli (src/device-plane/agent) through the scenarios.

Every scenario talks to an agent only through these methods, so another agent (the Go
meteorcli) plugs in with its own adapter:

    configure(server, api_key)     save the API address and an enrollment API key
    register(server, token)        meteorcli register --token ...
    test()                         meteorcli test (checks the API key)
    request_token()                meteorcli request-token --wait 0
    claim()                        meteorcli claim (polls until approved)
    run(heartbeats, between)       the background service, stopped after N heartbeats;
                                   between(n) runs after heartbeat n (instead of sleeping)

Each returns the exit code where the CLI has one. The agent runs in-process with its
real HTTP and MQTT clients; only host work (docker, Wi-Fi, WireGuard, reboot) goes to
the SimulatedDevice, and slow timers are shortened.
"""

from __future__ import annotations

import functools
import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from harness.device import SimulatedDevice
from harness.script import ssh_key

WG_PRIVATE = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
WG_PUBLIC = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="
SSH_HOST_KEY = ssh_key("contract host key", "root@device")


class PythonAgent:
    name = "python"
    features = frozenset({"mqtt", "actions", "wifi", "wireguard", "docker"})

    def __init__(self, monkeypatch: pytest.MonkeyPatch, workdir: Path, device: SimulatedDevice) -> None:
        self.config_dir = workdir / "meteorcli"
        self.device = device
        self._mp = monkeypatch
        self._isolate_host(workdir)

    # ------------------------------------------------------------------ commands
    def _cli(self, *args: str) -> int:
        from meteorcli import cli

        return int(cli.main(["--config-dir", str(self.config_dir), *args]))

    def configure(self, server: str, api_key: str) -> int:
        return self._cli("config", "--api-base", server, "--api-key", api_key)

    def register(self, server: str, token: str) -> int:
        return self._cli("register", "--server", server, "--token", token)

    def test(self) -> int:
        return self._cli("test")

    def request_token(self) -> int:
        return self._cli("request-token", "--wait", "0")

    def claim(self) -> int:
        return self._cli("claim", "--wait", "120")

    def run(self, heartbeats: int, between: Callable[[int], None] | None = None) -> int:
        from edge_agent.heartbeat import run_loop
        from meteorcli import cli

        done = {"beats": 0}

        def pause(_seconds: float) -> None:
            done["beats"] += 1
            if between is not None:
                between(done["beats"])

        def bounded_loop(client: Any, token: str, **kwargs: Any) -> None:
            kwargs.update(sleep=pause, should_continue=lambda: done["beats"] < heartbeats)
            run_loop(client, token, **kwargs)

        self._mp.setattr(cli, "run_loop", bounded_loop)
        return self._cli("run")

    @property
    def device_id(self) -> str | None:
        try:
            return json.loads((self.config_dir / "config.json").read_text(encoding="utf-8")).get("device_id")
        except OSError:
            return None

    # ------------------------------------------------------------------ the host
    def _isolate_host(self, workdir: Path) -> None:
        import runtime.compose
        import runtime.docker
        import runtime.systemd
        from edge_agent import mqtt, observer, service, wireguard
        from edge_agent.actions import ActionSync
        from edge_agent.wifi import WifiSync
        from meteorcli import cli

        mp, device = self._mp, self.device
        # Not a systemd service, no unit file, nothing to chown.
        mp.setattr(service, "running_as_service", lambda: False)
        mp.setattr(cli, "running_as_service", lambda: False)
        mp.setattr(cli, "service_state", lambda: cli.NOT_INSTALLED)
        mp.setattr(cli, "chown_config_dir", lambda *_a, **_k: None)
        mp.setattr("meteorcli.config.SERVICE_UNIT", workdir / "no-unit" / "meteorcli.service")
        # Approval polling waits 10 s between polls: skip the wait, keep the order.
        mp.setattr(cli, "time", types.SimpleNamespace(sleep=lambda _s: None, monotonic=_fast_clock(), time=_now))

        # What the simulated device has: every runtime and tool.
        for module, name in (
            (runtime.systemd, "systemd_available"),
            (runtime.docker, "docker_available"),
            (runtime.compose, "compose_available"),
            (wireguard, "wireguard_available"),
            (wireguard, "sshd_available"),
        ):
            mp.setattr(module, name, lambda: True)

        # Docker goes to the simulated device.
        class SimulatedDocker(runtime.docker.DockerAdapter):
            def __init__(self, *, runner: Any = None) -> None:
                super().__init__(runner=runner or device.docker)

        mp.setattr(runtime.docker, "DockerAdapter", SimulatedDocker)

        # WireGuard: a fixed key pair, commands recorded, authorized_keys in the workdir.
        wg_dir = self.config_dir / "wireguard"
        wg_dir.mkdir(parents=True, exist_ok=True)
        (wg_dir / "private.key").write_text(WG_PRIVATE + "\n", encoding="utf-8")
        (wg_dir / "public.key").write_text(WG_PUBLIC + "\n", encoding="utf-8")
        mp.setattr(wireguard, "sync_connectivity", functools.partial(wireguard.sync_connectivity, runner=device.wireguard))
        mp.setattr(wireguard, "local_ssh_user", lambda: "pi")
        mp.setattr(wireguard, "local_ssh_host_key", lambda *_a: SSH_HOST_KEY)
        mp.setattr(wireguard, "ssh_authorized_keys_path", lambda *_a: workdir / "authorized_keys")
        mp.setattr(wireguard, "keep_tunnel_up", lambda *_a, **_k: None)

        # Wi-Fi and one-off actions: the helpers are the simulated device.
        mp.setattr(cli, "WifiSync", functools.partial(
            WifiSync, runner=device.wifi_helper, installed=lambda: True,
            uplink=lambda: {"uplink_type": "wifi", "uplink_interface": "wlan0"}, sleep=lambda _s: None,
        ))
        mp.setattr(cli, "ActionSync", functools.partial(ActionSync, host=device))

        # MQTT: app and journal watching on the simulated device; metrics every second.
        def simulated_observer() -> observer.DeviceObserver:
            warnings = observer.WarningForwarder(runner=device.journal_helper)
            warnings.available = lambda: True
            return observer.DeviceObserver(apps=observer.AppObserver(runner=device.docker), warnings=warnings)

        mp.setattr(mqtt, "DeviceObserver", simulated_observer)
        mp.setattr(mqtt, "METRICS_INTERVAL_SECONDS", 1.0)


def _now() -> float:
    import time

    return time.time()


def _fast_clock() -> Callable[[], float]:
    """A monotonic clock that jumps 1 s per read, so poll deadlines pass without waiting."""
    state = {"t": 0.0}

    def tick() -> float:
        state["t"] += 1.0
        return state["t"]

    return tick
