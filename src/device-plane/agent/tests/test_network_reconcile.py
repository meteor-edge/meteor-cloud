"""Agent WiFi reconcile: revision skip, failures do not raise."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from edge_agent.network import reconcile_network


class FakeClient:
    def __init__(self, desired: dict[str, Any]) -> None:
        self.desired = desired

    def get_network(self, *, device_token: str) -> dict[str, Any]:
        return self.desired


def test_skips_when_revision_already_in_sync(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "wifi-applied.json"
    state.write_text(
        '{"revision": 3, "applied_ssid": "Lab", "sync_status": "in_sync"}',
        encoding="utf-8",
    )
    called = {"n": 0}

    def boom(**_kwargs):
        called["n"] += 1
        raise AssertionError("should not apply")

    monkeypatch.setattr("runtime.wifi.apply_wifi", boom)
    report = reconcile_network(
        FakeClient(
            {
                "configured": True,
                "revision": 3,
                "ssid": "Lab",
                "psk": "password12",
                "interface": "wlan0",
                "hidden": False,
            }
        ),
        "dev_token",
        tmp_path,
    )
    assert report is not None
    assert report["sync_status"] == "in_sync"
    assert called["n"] == 0


def test_failed_apply_returns_report(tmp_path: Path, monkeypatch) -> None:
    def fail(**_kwargs):
        raise RuntimeError("nmcli blew up")

    monkeypatch.setattr("runtime.wifi.apply_wifi", fail)
    report = reconcile_network(
        FakeClient(
            {
                "configured": True,
                "revision": 1,
                "ssid": "Lab",
                "psk": "password12",
                "interface": "wlan0",
                "hidden": False,
            }
        ),
        "dev_token",
        tmp_path,
    )
    assert report is not None
    assert report["sync_status"] == "failed"
    assert "nmcli" in (report["sync_error"] or "")
