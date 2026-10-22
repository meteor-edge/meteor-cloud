"""Device WireGuard helpers: private key never leaves the device."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from edge_agent.wireguard import install_gateway_ssh_key, sync_connectivity, write_client_config


def test_write_client_config_keeps_private_key_local(tmp_path: Path) -> None:
    conf = write_client_config(
        tmp_path,
        private_key="DEVICEPRIVATE",
        address="10.8.0.12/32",
        server_public_key="SERVERPUBLIC",
        server_endpoint="gw.example:51820",
    )
    text = conf.read_text(encoding="utf-8")
    assert "PrivateKey = DEVICEPRIVATE" in text
    assert "PublicKey = SERVERPUBLIC" in text


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

    with patch("edge_agent.wireguard.wireguard_available", return_value=True):
        response = sync_connectivity(client, "dev_token", directory, runner=runner)

    assert response is not None
    client.register_wireguard.assert_called_once_with(device_token="dev_token", public_key="PUBLICKEY")
    assert "SECRETKEY" not in str(client.register_wireguard.call_args)


def test_install_gateway_ssh_key_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "authorized_keys"
    install_gateway_ssh_key("ssh-ed25519 AAAA gw", authorized_keys=path)
    install_gateway_ssh_key("ssh-ed25519 AAAA gw", authorized_keys=path)
    assert path.read_text(encoding="utf-8").count("AAAA gw") == 1


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _prepare_keys(directory: Path) -> None:
    wg = directory / "wireguard"
    wg.mkdir(parents=True)
    (wg / "private.key").write_text("SECRETKEY\n", encoding="utf-8")
    (wg / "public.key").write_text("PUBLICKEY\n", encoding="utf-8")


def _recording_runner(calls: list[list[str]], *, interface_up: list[bool]):
    import subprocess

    def runner(args, **kwargs):
        del kwargs
        calls.append(list(args))
        if args[:2] == ["wg", "show"] and not interface_up[0]:
            raise subprocess.CalledProcessError(1, args)
        if args[:2] == ["wg-quick", "up"]:
            interface_up[0] = True
        if args[:2] == ["wg-quick", "down"]:
            interface_up[0] = False
        result = MagicMock()
        result.stdout = ""
        return result

    return runner


def _response() -> dict:
    return {
        "wg_address": "10.8.0.9/32",
        "server_public_key": "SERVER",
        "server_endpoint": "gw:51820",
        "server_allowed_ips": "10.8.0.1/32",
        "persistent_keepalive": 25,
    }


def test_connectivity_sync_does_not_flap_tunnel(tmp_path: Path) -> None:
    from edge_agent.wireguard import ConnectivitySync

    directory = tmp_path / "meteorcli"
    _prepare_keys(directory)
    client = MagicMock()
    client.register_wireguard.return_value = _response()
    calls: list[list[str]] = []
    up = [False]
    clock = _Clock()
    sync = ConnectivitySync(
        directory,
        runner=_recording_runner(calls, interface_up=up),
        register_interval_seconds=600,
        clock=clock,
    )
    with patch("edge_agent.wireguard.wireguard_available", return_value=True):
        for _ in range(5):
            sync.sync(client, "tok")
            clock.now += 30
    assert client.register_wireguard.call_count == 1
    assert sum(1 for c in calls if c[:2] == ["wg-quick", "up"]) == 1

    # Registration is refreshed after the interval, but an unchanged config is not reapplied.
    clock.now += 600
    with patch("edge_agent.wireguard.wireguard_available", return_value=True):
        sync.sync(client, "tok")
    assert client.register_wireguard.call_count == 2
    assert sum(1 for c in calls if c[:2] == ["wg-quick", "up"]) == 1

    # If the interface goes away it is brought back.
    up[0] = False
    with patch("edge_agent.wireguard.wireguard_available", return_value=True):
        sync.sync(client, "tok")
    assert sum(1 for c in calls if c[:2] == ["wg-quick", "up"]) == 2


def test_connectivity_sync_tears_down_when_revoked(tmp_path: Path) -> None:
    from edge_agent.client import AgentApiError
    from edge_agent.wireguard import ConnectivitySync

    directory = tmp_path / "meteorcli"
    _prepare_keys(directory)
    client = MagicMock()
    client.register_wireguard.return_value = _response()
    calls: list[list[str]] = []
    up = [False]
    clock = _Clock()
    sync = ConnectivitySync(
        directory, runner=_recording_runner(calls, interface_up=up), clock=clock
    )
    with patch("edge_agent.wireguard.wireguard_available", return_value=True):
        assert sync.sync(client, "tok") is not None
        assert up[0]
        client.register_wireguard.side_effect = AgentApiError(403, "connectivity_revoked", "revoked")
        clock.now += 601
        assert sync.sync(client, "tok") is None
    assert sync.revoked
    assert not up[0]
    assert not (directory / "wireguard" / "wg0.conf").exists()


def test_gateway_key_pinned_to_source(tmp_path: Path) -> None:
    path = tmp_path / "authorized_keys"
    install_gateway_ssh_key("ssh-ed25519 AAAA gw", authorized_keys=path, source_cidr="10.8.0.1/32")
    assert path.read_text(encoding="utf-8") == 'from="10.8.0.1/32" ssh-ed25519 AAAA gw\n'
