"""Safe agent update: version compare and ActionSync report-back."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from edge_agent.actions import ActionSync
from edge_agent.agent_update import settle_pending, start_updater, version_is_newer


class FakeClient:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.reported_actions: list[dict[str, Any]] = []

    def get_actions(self, *, device_token: str) -> list[dict[str, Any]]:
        out = self.actions
        self.actions = []
        return out

    def report_actions(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.reported_actions.append(payload)
        return {"accepted": len(payload.get("results", []))}


def test_version_is_newer() -> None:
    assert version_is_newer("0.3.4", "0.3.3")
    assert version_is_newer("0.3.10", "0.3.9")
    assert not version_is_newer("0.3.3", "0.3.3")
    assert not version_is_newer("0.3.3", "0.4.0")
    assert version_is_newer("0.3.4", None)
    assert not version_is_newer(None, "0.3.3")


def test_start_updater_fails_clearly_without_a_script(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("METEORCLI_UPDATE_SCRIPT", raising=False)
    monkeypatch.setattr("edge_agent.agent_update.find_updater_script", lambda: None)
    error = start_updater(config_dir=tmp_path, action_id="act-1", from_version="0.3.3")
    assert error is not None
    assert "updater is not installed" in error
    assert not (tmp_path / "pending-agent-update.json").exists()


def test_settle_pending_reports_done_when_version_advanced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("edge_agent.agent_update.current_agent_version", lambda: "0.3.4")
    monkeypatch.setattr("edge_agent.agent_update.updater_lock_held", lambda: False)
    (tmp_path / "pending-agent-update.json").write_text(
        '{"action_id": "act-1", "from_version": "0.3.3", "started_at": 1}\n',
        encoding="utf-8",
    )
    result = settle_pending(tmp_path)
    assert result == {"id": "act-1", "state": "done"}
    assert not (tmp_path / "pending-agent-update.json").exists()


def test_settle_pending_reports_failed_when_version_unchanged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("edge_agent.agent_update.current_agent_version", lambda: "0.3.3")
    monkeypatch.setattr("edge_agent.agent_update.updater_lock_held", lambda: False)
    (tmp_path / "pending-agent-update.json").write_text(
        '{"action_id": "act-2", "from_version": "0.3.3", "started_at": 1}\n',
        encoding="utf-8",
    )
    result = settle_pending(tmp_path)
    assert result is not None
    assert result["id"] == "act-2"
    assert result["state"] == "failed"
    assert "stayed at 0.3.3" in result["error"]


def test_action_sync_starts_updater_and_reports_later(tmp_path: Path, monkeypatch) -> None:
    client = FakeClient()
    script = tmp_path / "update.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("METEORCLI_UPDATE_SCRIPT", str(script))
    started: list[tuple] = []

    def fake_start(*, config_dir, action_id, from_version):
        started.append((config_dir, action_id, from_version))
        (config_dir / "pending-agent-update.json").write_text(
            f'{{"action_id": "{action_id}", "from_version": "{from_version}", "started_at": 1}}\n',
            encoding="utf-8",
        )
        return None

    monkeypatch.setattr("edge_agent.actions.start_updater", fake_start)
    monkeypatch.setattr("edge_agent.actions.settle_pending", lambda _dir: None)

    client.actions = [
        {"id": "act-9", "kind": "update_agent", "from_version": "0.3.3"},
    ]
    sync = ActionSync(client, "tok", config_dir=tmp_path, installed=lambda: True)
    sync.on_heartbeat({"actions_pending": True})
    assert started == [(tmp_path, "act-9", "0.3.3")]
    assert client.reported_actions == []

    monkeypatch.setattr(
        "edge_agent.actions.settle_pending",
        lambda _dir: {"id": "act-9", "state": "done"},
    )
    sync.on_heartbeat({"actions_pending": False})
    assert client.reported_actions == [{"results": [{"id": "act-9", "state": "done"}]}]
