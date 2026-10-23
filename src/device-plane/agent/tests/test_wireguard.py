"""Device WireGuard helpers: private key never leaves the device."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from edge_agent.client import AgentApiError
from edge_agent.wireguard import (
    apply_interface,
    install_gateway_ssh_key,
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
    client.register_wireguard.assert_called_once_with(device_token="dev_token", public_key="PUBLICKEY")
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
