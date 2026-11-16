"""Tests for the agent CLI."""

from __future__ import annotations

from edge_agent import main as main_module
from tests.conftest import FakeClient


def test_register_run_info_flow(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "agent"
    client = FakeClient()
    monkeypatch.setattr(main_module, "EdgeClient", lambda server_url, **_: client)

    # register
    code = main_module.main(
        [
            "--config-dir",
            str(config_dir),
            "register",
            "--server",
            "http://localhost:8000",
            "--token",
            "reg_abc",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Registered device device-1" in out
    # The secret must never be printed.
    assert "dev_secret-value" not in out

    # info
    code = main_module.main(["--config-dir", str(config_dir), "info"])
    assert code == 0
    info_out = capsys.readouterr().out
    assert "Device ID: device-1" in info_out
    assert "Credential present: yes" in info_out

    # run --once
    code = main_module.main(["--config-dir", str(config_dir), "run", "--once"])
    assert code == 0
    run_out = capsys.readouterr().out
    assert "status: online" in run_out


def test_register_with_token_file(tmp_path, monkeypatch, capsys) -> None:
    config_dir = tmp_path / "agent"
    token_file = tmp_path / "reg-token"
    token_file.write_text("reg_from_file", encoding="utf-8")
    client = FakeClient()
    monkeypatch.setattr(main_module, "EdgeClient", lambda server_url, **_: client)

    code = main_module.main(
        [
            "--config-dir",
            str(config_dir),
            "register",
            "--server",
            "http://localhost:8000",
            "--token-file",
            str(token_file),
        ]
    )
    assert code == 0
    assert client.register_calls[0]["token"] == "reg_from_file"
    # First registration: no device token to prove anything with.
    assert client.register_calls[0]["device_token"] is None
    # Token file consumed after success.
    assert not token_file.exists()

    # Registering again sends the device token it now holds, so the server accepts it as the same device.
    token_file.write_text("reg_again\n", encoding="utf-8")
    code = main_module.main(
        [
            "--config-dir",
            str(config_dir),
            "register",
            "--server",
            "http://localhost:8000",
            "--token-file",
            str(token_file),
        ]
    )
    assert code == 0
    assert client.register_calls[1]["device_token"] == client.register_response["device_token"]


def test_info_without_registration(tmp_path, capsys) -> None:
    code = main_module.main(["--config-dir", str(tmp_path / "empty"), "info"])
    assert code == 1
    assert "not registered" in capsys.readouterr().out


def test_run_without_registration(tmp_path, capsys) -> None:
    code = main_module.main(["--config-dir", str(tmp_path / "empty"), "run", "--once"])
    assert code == 1
    assert "not registered" in capsys.readouterr().err


def test_run_rejects_config_without_device_id(tmp_path, capsys) -> None:
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        '{"server_url":"http://localhost:8000","device_id":"","organization_id":"org"}',
        encoding="utf-8",
    )
    (config_dir / "device-token").write_text("dev_secret", encoding="utf-8")
    code = main_module.main(["--config-dir", str(config_dir), "run", "--once"])
    assert code == 1
    assert "Device id is missing" in capsys.readouterr().err
