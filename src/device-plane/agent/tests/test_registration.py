"""Tests for the registration workflow."""

from __future__ import annotations

import stat

import pytest

from edge_agent.client import AgentApiError
from edge_agent.config import load_config
from edge_agent.credentials import read_device_token
from edge_agent.registration import register
from tests.conftest import FakeClient


def test_register_persists_credential_and_config(agent_paths) -> None:
    client = FakeClient()
    result = register(client=client, paths=agent_paths, token="reg_abc", name="edge-01")

    assert result.device_id == "device-1"
    assert read_device_token(agent_paths.token_path) == "dev_secret-value"
    mode = stat.S_IMODE(agent_paths.token_path.stat().st_mode)
    assert mode == 0o600

    config = load_config(agent_paths)
    assert config is not None
    assert config.device_id == "device-1"
    assert config.heartbeat_interval_seconds == 60


def test_register_removes_token_file_after_success(agent_paths, tmp_path) -> None:
    token_file = tmp_path / "reg-token"
    token_file.write_text("reg_abc", encoding="utf-8")
    client = FakeClient()

    register(
        client=client,
        paths=agent_paths,
        token="reg_abc",
        token_file=token_file,
    )
    assert not token_file.exists()


def test_register_keeps_token_file_on_failure(agent_paths, tmp_path) -> None:
    token_file = tmp_path / "reg-token"
    token_file.write_text("reg_abc", encoding="utf-8")
    client = FakeClient(
        register_error=AgentApiError(401, "invalid_registration_token", "bad token")
    )

    with pytest.raises(AgentApiError):
        register(
            client=client,
            paths=agent_paths,
            token="reg_abc",
            token_file=token_file,
        )
    # Failed registration must not consume the token file or write a credential.
    assert token_file.exists()
    assert read_device_token(agent_paths.token_path) is None


def test_register_does_not_expose_secret_in_logs(agent_paths, caplog) -> None:
    client = FakeClient()
    with caplog.at_level("INFO", logger="edge_agent"):
        register(client=client, paths=agent_paths, token="reg_abc")
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "dev_secret-value" not in joined
    assert "reg_abc" not in joined
