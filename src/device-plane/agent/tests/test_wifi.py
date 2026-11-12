"""Agent side of console Wi-Fi changes: apply, verify the server is reachable, confirm or roll back, report."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from edge_agent import wifi
from edge_agent.client import AgentApiError
from edge_agent.heartbeat import run_loop
from edge_agent.wifi import WifiSync

STATUS = {
    "supported": True,
    "unsupported_reason": None,
    "connected_ssid": "Home",
    "signal_percent": 70,
    "interface": "wlan0",
    "wired_connected": False,
    "saved_networks": [{"ssid": "Home", "managed": False}],
}


class FakeClient:
    def __init__(self, changes: list[dict[str, Any]] | None = None, *, reachable: bool = True) -> None:
        self.changes = changes or []
        self.reachable = reachable
        self.reports: list[dict[str, Any]] = []
        self.report_error: AgentApiError | None = None
        self.fetches = 0

    def health(self) -> dict[str, Any]:
        if not self.reachable:
            raise OSError("no route")
        return {"status": "ok"}

    def get_wifi_changes(self, *, device_token: str) -> list[dict[str, Any]]:
        self.fetches += 1
        changes, self.changes = self.changes, []
        return changes

    def report_wifi(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.report_error is not None:
            raise self.report_error
        self.reports.append(json.loads(json.dumps(payload)))
        return {"accepted": len(payload.get("results", []))}

    def heartbeat(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "online", "wifi_pending": True}


class FakeHelper:
    def __init__(self, *, apply_code: int = 0, apply_error: str = "", pending: dict | None = None) -> None:
        self.apply_code = apply_code
        self.apply_error = apply_error
        self.pending = pending
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, args: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        command = args[-1]
        self.calls.append((command, json.loads(stdin) if stdin else None))
        code, out = 0, {}
        if command == "status":
            out = dict(STATUS)
            if self.pending:
                out["pending"] = self.pending
        elif command == "apply":
            code = self.apply_code
            out = {"error": self.apply_error} if code else {"applied": True}
        elif command == "rollback":
            self.pending = None
        return subprocess.CompletedProcess(list(args), code, json.dumps(out), "")

    def commands(self) -> list[str]:
        return [name for name, _request in self.calls if name != "status"]


def _change(**extra: Any) -> dict[str, Any]:
    return {
        "id": "5b0c1a52-7c0e-4f39-9a52-0d5b1f6a9c11",
        "revision": 2,
        "action": "apply",
        "ssid": "Office",
        "security": "wpa-psk",
        "hidden": False,
        "connect_now": False,
        "password": "office-secret",
        **extra,
    }


def _sync(client: FakeClient, helper: FakeHelper, clock: list[float] | None = None) -> WifiSync:
    now = clock if clock is not None else [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return WifiSync(client, "token", runner=helper, installed=lambda: True, sleep=sleep, clock=lambda: now[0])


def test_first_heartbeat_reports_wifi_status_then_only_changes() -> None:
    client, helper = FakeClient(), FakeHelper()
    clock = [0.0]
    sync = _sync(client, helper, clock)
    sync.on_heartbeat({"status": "online"})
    sync.on_heartbeat({"status": "online"})
    assert client.reports == [{"results": [], "status": STATUS}]
    assert client.fetches == 0
    clock[0] += wifi.STATUS_REFRESH_SECONDS
    sync.on_heartbeat({"status": "online"})
    assert len(client.reports) == 2


def test_saved_for_later_is_applied_and_reported() -> None:
    client, helper = FakeClient([_change()]), FakeHelper()
    _sync(client, helper).on_heartbeat({"wifi_pending": True})
    assert helper.commands() == ["apply"]
    request = next(request for name, request in helper.calls if name == "apply")
    assert request["password"] == "office-secret" and request["connect_now"] is False
    assert client.reports[-1]["results"] == [{"id": _change()["id"], "revision": 2, "state": "applied"}]
    assert "office-secret" not in json.dumps(client.reports)


def test_connect_now_is_confirmed_when_the_server_answers() -> None:
    client, helper = FakeClient([_change(connect_now=True)]), FakeHelper()
    _sync(client, helper).on_heartbeat({"wifi_pending": True})
    assert helper.commands() == ["apply", "confirm"]
    assert client.reports[-1]["results"][0]["state"] == "applied"


def test_connect_now_rolls_back_when_the_server_is_unreachable() -> None:
    client, helper = FakeClient([_change(connect_now=True)], reachable=False), FakeHelper()
    clock = [0.0]
    sync = _sync(client, helper, clock)
    client.report_error = AgentApiError(0, "network_error", "offline")
    sync.on_heartbeat({"wifi_pending": True})
    assert helper.commands() == ["apply", "rollback"]
    assert clock[0] >= wifi.VERIFY_SECONDS
    assert client.reports == []

    # Back on the old network: the result is still delivered with the next heartbeat.
    client.report_error = None
    client.reachable = True
    sync.on_heartbeat({"status": "online"})
    result = client.reports[-1]["results"][0]
    assert result["state"] == "rolled_back"
    assert "could not reach MeteorCloud" in result["error"]


def test_a_network_that_cannot_be_joined_is_reported_as_rolled_back() -> None:
    helper = FakeHelper(apply_code=wifi.EXIT_NOT_JOINED, apply_error="The device could not join this network.")
    client = FakeClient([_change(connect_now=True)])
    _sync(client, helper).on_heartbeat({"wifi_pending": True})
    assert helper.commands() == ["apply"]
    assert client.reports[-1]["results"][0] == {
        "id": _change()["id"],
        "revision": 2,
        "state": "rolled_back",
        "error": "The device could not join this network.",
    }


def test_remove_and_helper_refusal() -> None:
    client, helper = FakeClient([_change(action="remove", password=None)]), FakeHelper()
    _sync(client, helper).on_heartbeat({"wifi_pending": True})
    assert ("remove", {"ssid": "Office"}) in helper.calls
    assert client.reports[-1]["results"][0]["state"] == "removed"

    refused = FakeHelper(apply_code=5, apply_error="This device has no Wi-Fi adapter.")
    client = FakeClient([_change()])
    _sync(client, refused).on_heartbeat({"wifi_pending": True})
    assert client.reports[-1]["results"][0]["state"] == "failed"


def test_unconfirmed_change_from_before_a_restart_is_rolled_back() -> None:
    pending = {"id": _change()["id"], "revision": 2}
    client, helper = FakeClient(), FakeHelper(pending=pending)
    _sync(client, helper).on_heartbeat({"status": "online"})
    assert helper.commands() == ["rollback"]
    result = client.reports[-1]["results"][0]
    assert result["state"] == "rolled_back" and "restarted" in result["error"]


def test_missing_helper_reports_unsupported_and_fails_changes() -> None:
    client = FakeClient([_change()])
    sync = WifiSync(client, "token", runner=FakeHelper(), installed=lambda: False, clock=lambda: 0.0)
    sync.on_heartbeat({"wifi_pending": True})
    report = client.reports[-1]
    assert report["status"]["supported"] is False
    assert "installer" in report["status"]["unsupported_reason"]
    assert report["results"][0]["state"] == "failed"


def test_older_server_and_errors_never_stop_the_heartbeat_loop(caplog: pytest.LogCaptureFixture) -> None:
    client, helper = FakeClient(), FakeHelper()
    client.report_error = AgentApiError(404, "not_found", "Not Found")
    _sync(client, helper).on_heartbeat({"status": "online"})
    assert caplog.records == []

    def boom(args: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("bug")

    WifiSync(FakeClient(), "token", runner=boom, installed=lambda: True).on_heartbeat({})
    assert "Wi-Fi sync failed" in caplog.text


def test_run_loop_hands_each_response_to_the_hook() -> None:
    seen: list[dict[str, Any]] = []
    rounds = iter([True, True, False])
    run_loop(
        FakeClient(),  # type: ignore[arg-type]
        "token",
        interval_seconds=1,
        sleep=lambda _seconds: None,
        should_continue=lambda: next(rounds),
        on_response=seen.append,
    )
    assert seen == [{"status": "online", "wifi_pending": True}] * 2
