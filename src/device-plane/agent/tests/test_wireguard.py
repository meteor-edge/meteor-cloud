"""Device WireGuard helpers: private key never leaves the device."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from edge_agent.client import AgentApiError
from edge_agent.wireguard import (
    REGISTER_REFRESH_SECONDS,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    ConnectivityState,
    _elevate_wireguard,
    _try_chmod,
    apply_interface,
    ensure_keypair,
    install_gateway_ssh_key,
    local_ssh_host_key,
    local_ssh_user,
    refresh_endpoint,
    sync_connectivity,
    write_client_config,
)


def test_write_client_config_keeps_private_key_local(tmp_path: Path) -> None:
    conf, changed = write_client_config(
        tmp_path,
        private_key="DEVICEPRIVATE",
        address="10.8.0.12/32",
        server_public_key="SERVERPUBLIC",
        server_endpoint="gw.example:51820",
    )
    assert changed is True
    text = conf.read_text(encoding="utf-8")
    assert "PrivateKey = DEVICEPRIVATE" in text
    assert "PublicKey = SERVERPUBLIC" in text
    _, changed_again = write_client_config(
        tmp_path,
        private_key="DEVICEPRIVATE",
        address="10.8.0.12/32",
        server_public_key="SERVERPUBLIC",
        server_endpoint="gw.example:51820",
    )
    assert changed_again is False


def test_apply_interface_skips_cycle_when_config_unchanged(tmp_path: Path) -> None:
    conf, _changed = write_client_config(
        tmp_path,
        private_key="DEVICEPRIVATE",
        address="10.8.0.12/32",
        server_public_key="SERVERPUBLIC",
        server_endpoint="gw.example:51820",
    )
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        del kwargs
        calls.append(list(args))
        result = MagicMock()
        result.stdout = ""
        result.returncode = 0
        return result

    apply_interface(conf, runner=runner, config_changed=False)
    assert calls == [["wg", "show", "wg0"]]

    down_calls: list[list[str]] = []

    def runner_down(args, **kwargs):
        del kwargs
        down_calls.append(list(args))
        result = MagicMock()
        result.stdout = ""
        result.returncode = 0
        if args[:2] == ["wg", "show"]:
            raise subprocess.CalledProcessError(1, list(args))
        if args[:2] == ["wg-quick", "down"]:
            raise FileNotFoundError("down")
        return result

    apply_interface(conf, runner=runner_down, config_changed=False)
    assert ["wg-quick", "up", str(conf)] in down_calls

    apply_interface(conf, runner=runner, config_changed=True)
    assert ["wg-quick", "down", str(conf)] in calls
    assert ["wg-quick", "up", str(conf)] in calls


def test_sync_connectivity_does_not_upload_private_key(tmp_path: Path) -> None:
    client = MagicMock()
    client.register_wireguard.return_value = {
        "wg_address": "10.8.0.9/32",
        "server_public_key": "SERVER",
        "server_endpoint": "gw:51820",
        "persistent_keepalive": 25,
        "ssh_gateway_public_key": "ssh-ed25519 AAAA gateway",
    }
    directory = tmp_path / "meteorcli"
    wg = directory / "wireguard"
    wg.mkdir(parents=True)
    (wg / "private.key").write_text("SECRETKEY\n", encoding="utf-8")
    (wg / "public.key").write_text("PUBLICKEY\n", encoding="utf-8")

    def runner(args, **kwargs):
        del kwargs
        result = MagicMock()
        result.stdout = ""
        result.returncode = 0
        if args[:2] == ["wg-quick", "down"]:
            raise FileNotFoundError("down")
        return result

    with (
        patch("edge_agent.wireguard.wireguard_available", return_value=True),
        patch("edge_agent.wireguard.socket.getaddrinfo", side_effect=OSError("offline")),
    ):
        response = sync_connectivity(client, "dev_token", directory, runner=runner)

    assert response is not None
    client.register_wireguard.assert_called_once()
    assert client.register_wireguard.call_args.kwargs["public_key"] == "PUBLICKEY"
    assert "ssh_user" in client.register_wireguard.call_args.kwargs
    assert "SECRETKEY" not in str(client.register_wireguard.call_args)
    assert "SECRETKEY" not in str(client.register_wireguard.call_args)


def test_sync_connectivity_logs_disabled_at_info(tmp_path: Path, caplog) -> None:
    client = MagicMock()
    client.register_wireguard.side_effect = AgentApiError(403, "connectivity_disabled", "off")
    directory = tmp_path / "meteorcli"
    wg = directory / "wireguard"
    wg.mkdir(parents=True)
    (wg / "private.key").write_text("SECRETKEY\n", encoding="utf-8")
    (wg / "public.key").write_text("PUBLICKEY\n", encoding="utf-8")

    with (
        patch("edge_agent.wireguard.wireguard_available", return_value=True),
        caplog.at_level(logging.INFO, logger="edge_agent"),
    ):
        response = sync_connectivity(client, "dev_token", directory)

    assert response is None
    assert "WireGuard registration failed" not in caplog.text
    assert "disabled" in caplog.text.lower()


def test_install_gateway_ssh_key_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "authorized_keys"
    install_gateway_ssh_key("ssh-ed25519 AAAA gw", authorized_keys=path)
    install_gateway_ssh_key("ssh-ed25519 AAAA gw", authorized_keys=path)
    assert path.read_text(encoding="utf-8").count("AAAA gw") == 1


def _wg(show: dict[str, str], calls: list[list[str]]):
    """A fake runner: `wg show wg0 <field>` prints show[field]; every call is recorded."""

    def runner(args, **kwargs):
        del kwargs
        calls.append(list(args))
        result = MagicMock()
        result.stdout = show.get(args[3], "") if args[:2] == ["wg", "show"] else ""
        return result

    return runner


def test_refresh_endpoint_follows_a_new_server_ip() -> None:
    calls: list[list[str]] = []
    runner = _wg({"latest-handshakes": "SERVER\t1000\n", "endpoints": "SERVER\t203.0.113.1:51820\n"}, calls)
    refresh_endpoint("wg0", server_public_key="SERVER", server_endpoint="wg.example.com:51820",
                     runner=runner, resolve=lambda host: "203.0.113.9", now=lambda: 1000 + 600)
    assert calls[-1] == ["wg", "set", "wg0", "peer", "SERVER", "endpoint", "203.0.113.9:51820"]


def test_refresh_endpoint_leaves_a_healthy_tunnel_alone() -> None:
    calls: list[list[str]] = []
    runner = _wg({"latest-handshakes": "SERVER\t1000\n"}, calls)
    refresh_endpoint("wg0", server_public_key="SERVER", server_endpoint="wg.example.com:51820",
                     runner=runner, resolve=lambda host: "203.0.113.9", now=lambda: 1000 + 30)
    assert calls == [["wg", "show", "wg0", "latest-handshakes"]]


def test_refresh_endpoint_skips_ip_endpoints_and_unchanged_names() -> None:
    calls: list[list[str]] = []
    refresh_endpoint("wg0", server_public_key="SERVER", server_endpoint="203.0.113.1:51820",
                     runner=_wg({}, calls))
    assert calls == []

    runner = _wg({"latest-handshakes": "SERVER\t0\n", "endpoints": "SERVER\t203.0.113.1:51820\n"}, calls)
    refresh_endpoint("wg0", server_public_key="SERVER", server_endpoint="wg.example.com:51820",
                     runner=runner, resolve=lambda host: "203.0.113.1", now=lambda: 5000)
    assert not any(call[:2] == ["wg", "set"] for call in calls)


def test_local_ssh_user_prefers_sudo_user(monkeypatch) -> None:
    monkeypatch.setattr("edge_agent.wireguard.os.geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "pi")
    assert local_ssh_user() == "pi"


def test_elevate_wireguard_uses_sudo_only_for_privileged_commands(monkeypatch) -> None:
    monkeypatch.setattr("edge_agent.wireguard.os.geteuid", lambda: 1000)
    monkeypatch.setattr("edge_agent.wireguard.shutil.which", lambda name: f"/usr/bin/{name}")
    assert _elevate_wireguard(["wg", "genkey"]) == ["wg", "genkey"]
    assert _elevate_wireguard(["wg-quick", "up", "/tmp/wg0.conf"]) == [
        "sudo",
        "-n",
        "/usr/bin/wg-quick",
        "up",
        "/tmp/wg0.conf",
    ]
    assert _elevate_wireguard(["wg", "show", "wg0"]) == ["sudo", "-n", "/usr/bin/wg", "show", "wg0"]
    monkeypatch.setattr("edge_agent.wireguard.os.geteuid", lambda: 0)
    assert _elevate_wireguard(["wg-quick", "up", "/tmp/wg0.conf"]) == ["wg-quick", "up", "/tmp/wg0.conf"]


def test_try_chmod_ignores_permission_errors(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "dir"
    path.mkdir()

    def boom(_target, _mode):
        raise PermissionError("denied")

    monkeypatch.setattr("edge_agent.wireguard.os.chmod", boom)
    _try_chmod(path, 0o700)

    def runner(args, **kwargs):
        del kwargs
        result = MagicMock()
        result.stdout = "PRIVATE\n" if args == ["wg", "genkey"] else ""
        return result

    with patch("edge_agent.wireguard.subprocess.run") as pub:
        pub.return_value = MagicMock(stdout="PUBLIC\n")
        ensure_keypair(path, runner=runner)


# --- Registration schedule, backoff and self-healing --------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _registered_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "meteorcli"
    wg = directory / "wireguard"
    wg.mkdir(parents=True)
    (wg / "private.key").write_text("SECRETKEY\n", encoding="utf-8")
    (wg / "public.key").write_text("PUBLICKEY\n", encoding="utf-8")
    return directory


class _Wg:
    """Records wg / wg-quick calls; `wg show wg0` fails while the interface is down."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.up = False

    def __call__(self, args, **kwargs):
        del kwargs
        args = list(args)
        self.calls.append(args)
        result = MagicMock()
        result.stdout = ""
        if args[:2] == ["wg-quick", "up"]:
            self.up = True
        elif args[:2] == ["wg-quick", "down"]:
            self.up = False
        elif args[:3] == ["wg", "show", "wg0"] and len(args) == 3 and not self.up:
            raise subprocess.CalledProcessError(1, args)
        return result


def _sync(client, directory: Path, wg: _Wg, state: ConnectivityState):
    with (
        patch("edge_agent.wireguard.wireguard_available", return_value=True),
        patch("edge_agent.wireguard.local_ssh_host_key", return_value="ssh-ed25519 HOSTKEY"),
    ):
        return sync_connectivity(client, "dev_token", directory, runner=wg, state=state)


def test_registration_is_a_periodic_refresh_not_every_heartbeat(tmp_path: Path) -> None:
    from tests.conftest import FakeClient

    client = FakeClient()
    directory = _registered_dir(tmp_path)
    wg = _Wg()
    clock = _Clock()
    state = ConnectivityState(clock=clock, jitter=lambda low, high: 1.0)

    assert _sync(client, directory, wg, state) is not None
    assert len(client.wireguard_calls) == 1
    assert client.wireguard_calls[0]["ssh_host_key"] == "ssh-ed25519 HOSTKEY"
    assert "SECRETKEY" not in str(client.wireguard_calls)
    for _ in range(10):  # heartbeats within the refresh interval: no API calls
        clock.now += 60
        _sync(client, directory, wg, state)
    assert len(client.wireguard_calls) == 1
    clock.now += REGISTER_REFRESH_SECONDS
    _sync(client, directory, wg, state)
    assert len(client.wireguard_calls) == 2


def test_failed_registration_backs_off_and_keeps_tunnel_from_local_config(tmp_path: Path) -> None:
    from tests.conftest import FakeClient

    client = FakeClient()
    directory = _registered_dir(tmp_path)
    wg = _Wg()
    clock = _Clock()
    state = ConnectivityState(clock=clock, jitter=lambda low, high: high)
    _sync(client, directory, wg, state)
    assert wg.up

    # Device reboot during a control-plane outage: tunnel down, API unreachable.
    wg.up = False
    client.wireguard_error = AgentApiError(0, "network_error", "unreachable")
    clock.now = state.next_register_at
    _sync(client, directory, wg, state)
    assert wg.up, "tunnel must come back from the cached wg0.conf without the API"
    delays = []
    for _ in range(8):
        clock.now = state.next_register_at
        before = clock.now
        _sync(client, directory, wg, state)
        delays.append(state.next_register_at - before)
    assert delays[:3] == [60, 120, 240]  # exponential
    assert max(delays) == RETRY_MAX_SECONDS  # bounded

    # The API is back: one successful refresh resets the schedule.
    client.wireguard_error = None
    clock.now = state.next_register_at
    _sync(client, directory, wg, state)
    assert state.failures == 0


def test_backoff_jitter_stays_within_bounds() -> None:
    import random

    clock = _Clock()
    state = ConnectivityState(clock=clock, jitter=random.Random(3).uniform)
    for attempt in range(1, 10):
        delay = state.failed()
        ceiling = min(RETRY_BASE_SECONDS * 2 ** (attempt - 1), RETRY_MAX_SECONDS)
        assert ceiling * 0.5 <= delay <= ceiling


def test_revoked_device_stops_tunnel_and_waits_for_reenroll(tmp_path: Path, caplog) -> None:
    from tests.conftest import FakeClient

    client = FakeClient()
    directory = _registered_dir(tmp_path)
    wg = _Wg()
    clock = _Clock()
    state = ConnectivityState(clock=clock, jitter=lambda low, high: 1.0)
    _sync(client, directory, wg, state)
    assert wg.up

    client.wireguard_error = AgentApiError(403, "connectivity_revoked", "revoked")
    clock.now = state.next_register_at
    with caplog.at_level(logging.WARNING, logger="edge_agent"):
        _sync(client, directory, wg, state)
        clock.now += 60
        _sync(client, directory, wg, state)  # not due: no API call, tunnel not restarted
    assert not wg.up
    assert state.blocked == "connectivity_revoked"
    assert len(client.wireguard_calls) == 2
    assert caplog.text.count("re-enroll") == 1
    assert "Traceback" not in caplog.text

    # Operator re-enrolls: the next scheduled check reconnects on its own.
    client.wireguard_error = None
    clock.now = state.next_register_at
    _sync(client, directory, wg, state)
    assert wg.up
    assert state.blocked is None


def test_local_ssh_host_key_reads_public_key_only(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pub"
    ed = tmp_path / "ssh_host_ed25519_key.pub"
    ed.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 root@pi\n", encoding="utf-8")
    assert local_ssh_host_key([missing, ed]) == "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5"
    junk = tmp_path / "junk.pub"
    junk.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
    assert local_ssh_host_key([junk]) is None


def test_refresh_endpoint_follows_gateway_dns_change() -> None:
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        del kwargs
        calls.append(list(args))
        result = MagicMock()
        if "latest-handshakes" in args:
            result.stdout = "SERVER\t0\n"  # never: tunnel is not working
        elif "endpoints" in args:
            result.stdout = "SERVER\t198.51.100.1:51820\n"
        else:
            result.stdout = ""
        return result

    refresh_endpoint(
        "wg0", server_public_key="SERVER", server_endpoint="gw.example.com:51820",
        runner=runner, resolve=lambda host: "203.0.113.7", now=lambda: 10_000.0,
    )
    assert ["wg", "set", "wg0", "peer", "SERVER", "endpoint", "203.0.113.7:51820"] in calls

    # A temporary DNS failure keeps the current endpoint instead of crashing.
    def failing(host: str) -> str:
        raise OSError("Temporary failure in name resolution")

    calls.clear()
    refresh_endpoint(
        "wg0", server_public_key="SERVER", server_endpoint="gw.example.com:51820",
        runner=runner, resolve=failing, now=lambda: 10_000.0,
    )
    assert not any(call[:2] == ["wg", "set"] for call in calls)
