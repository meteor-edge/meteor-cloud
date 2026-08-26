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
    # Token file consumed after success.
    assert not token_file.exists()


def test_info_without_registration(tmp_path, capsys) -> None:
    code = main_module.main(["--config-dir", str(tmp_path / "empty"), "info"])
    assert code == 1
    assert "not registered" in capsys.readouterr().out


def test_run_without_registration(tmp_path, capsys) -> None:
    code = main_module.main(["--config-dir", str(tmp_path / "empty"), "run", "--once"])
    assert code == 1
    assert "not registered" in capsys.readouterr().err
