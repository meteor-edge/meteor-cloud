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
