"""Tests for secure credential and config persistence."""

from __future__ import annotations

import os
import stat

import pytest

from edge_agent.config import AgentConfig, load_config, save_config
from edge_agent.credentials import (
    read_device_token,
    read_registration_token_file,
    remove_file,
    write_device_token,
    write_secret_file,
)


def test_write_device_token_is_owner_only(agent_paths) -> None:
    write_device_token(agent_paths.token_path, "dev_secret")
    mode = stat.S_IMODE(agent_paths.token_path.stat().st_mode)
    assert mode == 0o600
    assert read_device_token(agent_paths.token_path) == "dev_secret"


def test_write_device_token_is_atomic_overwrite(agent_paths) -> None:
    write_device_token(agent_paths.token_path, "dev_one")
    write_device_token(agent_paths.token_path, "dev_two")
    assert read_device_token(agent_paths.token_path) == "dev_two"
    # No stray temp files left behind.
    leftovers = [
        p.name for p in agent_paths.token_path.parent.iterdir() if p.name.startswith(".tmp")
    ]
    assert leftovers == []


def test_write_secret_file_closes_fd_if_chmod_fails(tmp_path, monkeypatch) -> None:
    def fail_chmod(_path: str | os.PathLike[str], _mode: int) -> None:
        raise OSError("chmod failed")

    monkeypatch.setattr(os, "chmod", fail_chmod)
    target = tmp_path / "secret"
    with pytest.raises(OSError, match="chmod failed"):
        write_secret_file(target, "secret")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp-secret-")]
    assert leftovers == []


def test_read_missing_token_returns_none(agent_paths) -> None:
    assert read_device_token(agent_paths.token_path) is None


def test_registration_token_file_roundtrip(tmp_path) -> None:
    token_file = tmp_path / "reg-token"
    token_file.write_text("reg_value\n", encoding="utf-8")
    assert read_registration_token_file(token_file) == "reg_value"
    remove_file(token_file)
    assert not token_file.exists()
    # Removing a missing file is a no-op.
    remove_file(token_file)


def test_config_roundtrip(agent_paths) -> None:
    config = AgentConfig(
        server_url="http://localhost:8000",
        device_id="device-1",
        organization_id="org-1",
        name="edge-01",
        heartbeat_interval_seconds=90,
    )
    save_config(agent_paths, config)
    loaded = load_config(agent_paths)
    assert loaded == config


def test_damaged_files_are_treated_as_missing(tmp_path, caplog) -> None:
    from edge_agent.config import AgentPaths, load_config
    from edge_agent.credentials import read_device_token

    token = tmp_path / "device-token"
    token.write_bytes(b"\xff\xfe\x00\x00")
    assert read_device_token(token) is None

    paths = AgentPaths(config_path=tmp_path / "config.json", token_path=token)
    for damaged in ("", '{"server_url": "http://x"', "[1, 2]", '{"heartbeat_interval_seconds": "soon"}'):
        paths.config_path.write_text(damaged, encoding="utf-8")
        assert load_config(paths) is None
    assert caplog.text.count("is damaged and was ignored") == 1  # once, not on every check

    paths.config_path.write_text('{"server_url": "http://x", "heartbeat_interval_seconds": 0}', encoding="utf-8")
    assert load_config(paths).heartbeat_interval_seconds == 60
    paths.config_path.write_text('{"server_url": "http://x", "heartbeat_interval_seconds": 1}', encoding="utf-8")
    assert load_config(paths).heartbeat_interval_seconds == 5
