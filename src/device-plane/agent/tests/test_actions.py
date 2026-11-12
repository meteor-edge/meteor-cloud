"""One-off console actions: collect, apply through the helper, report."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from edge_agent.actions import ActionSync


class FakeClient:
    def __init__(self, actions: list[dict[str, Any]] | None = None) -> None:
        self.actions = list(actions or [])
        self.reports: list[dict[str, Any]] = []
        self.gets = 0

    def get_actions(self, *, device_token: str) -> list[dict[str, Any]]:
        self.gets += 1
        assert device_token == "tok"
        out = self.actions
        self.actions = []
        return out

    def report_actions(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert device_token == "tok"
        self.reports.append(payload)
        return {"accepted": len(payload.get("results", []))}


def _runner(answers: dict[str, tuple[int, dict]]) -> Any:
    def run(argv: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        command = argv[-1]
        code, body = answers[command]
        if stdin is not None:
            request = json.loads(stdin)
            assert "password" in request or command in {"reboot", "shutdown"}
        return subprocess.CompletedProcess(argv, code, json.dumps(body), "")

    return run


def test_password_change_is_reported_done() -> None:
    client = FakeClient(
        [
            {
                "id": "a1",
                "kind": "set_password",
                "username": "pi",
                "password": "secret-password",
                "sudo": False,
            }
        ]
    )
    sync = ActionSync(
        client,  # type: ignore[arg-type]
        "tok",
        runner=_runner({"set-password": (0, {"changed": True})}),
        installed=lambda: True,
    )
    sync.on_heartbeat({"actions_pending": True})
    assert client.gets == 1
    assert client.reports == [{"results": [{"id": "a1", "state": "done"}]}]


def test_restart_is_reported_before_the_helper_runs() -> None:
    order: list[str] = []

    def run(argv: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        order.append("helper")
        return subprocess.CompletedProcess(argv, 0, '{"started": true}', "")

    class OrderedClient(FakeClient):
        def report_actions(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
            order.append("report")
            return super().report_actions(device_token=device_token, payload=payload)

    client = OrderedClient([{"id": "a2", "kind": "reboot"}])
    ActionSync(client, "tok", runner=run, installed=lambda: True).on_heartbeat({"actions_pending": True})
    assert order == ["report", "helper"]
    assert client.reports[0]["results"][0] == {"id": "a2", "state": "done"}


def test_missing_helper_is_reported() -> None:
    client = FakeClient([{"id": "a3", "kind": "shutdown"}])
    ActionSync(client, "tok", installed=lambda: False).on_heartbeat({"actions_pending": True})
    # Reported as done before apply, then failed when the helper is missing.
    assert client.reports[-1]["results"][0]["state"] == "failed"
    assert "not installed" in client.reports[-1]["results"][0]["error"]
