"""Health-gated updates: wait for the new release, roll back to the previous one if it fails."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from runtime.docker import ContainerHealth

from edge_agent.health_gate import gate_settings, wait_until_healthy
from edge_agent.software import applied_state_path, reconcile_software

GATE = {"timeout_seconds": 30, "stable_seconds": 5}


def _item(version: str, revision: int, *, image: str, gate: dict[str, Any] | None = GATE) -> dict[str, Any]:
    spec: dict[str, Any] = {"container_name": "app", "image": image}
    if gate is not None:
        spec["health_check"] = gate
    return {
        "workload_key": "app",
        "kind": "docker",
        "release_id": f"rel-{version}",
        "release_version": version,
        "artifact_id": None,
        "revision": revision,
        "spec": spec,
    }


V1 = _item("1.0", 1, image="app:1")
V2 = _item("2.0", 2, image="app:2")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _wait(states: list[tuple[bool | None, str | None]], **kwargs: Any) -> str | None:
    clock = _Clock()
    feed = iter(states)
    last = states[-1]
    return wait_until_healthy(
        lambda: next(feed, last),
        timeout_seconds=kwargs.pop("timeout_seconds", 10),
        stable_seconds=kwargs.pop("stable_seconds", 4),
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )


def test_healthy_after_a_stable_window() -> None:
    assert _wait([(None, "starting"), (True, None)]) is None


def test_never_healthy_returns_the_last_problem() -> None:
    assert _wait([(False, "container app is not running (exited, exit 1)")]) == (
        "container app is not running (exited, exit 1)"
    )


def test_flapping_restarts_the_stable_window_and_fails() -> None:
    flapping = [(True, None), (False, "crashed"), (True, None), (False, "crashed")] * 10
    assert _wait(flapping) == "crashed"


def test_http_probe_must_pass_too() -> None:
    assert _wait([(True, None)], http_url="http://127.0.0.1:8080/health", probe=lambda url: "HTTP 500") == "HTTP 500"
    assert _wait([(True, None)], http_url="http://127.0.0.1:8080/health", probe=lambda url: None) is None


def test_gate_defaults() -> None:
    assert gate_settings({}) is None
    assert gate_settings({"health_check": {}}) == {
        "timeout_seconds": 60,
        "stable_seconds": 10,
        "http_url": None,
        "rollback": True,
    }


def _reconcile(tmp_path: Path, desired: dict[str, Any], *, gate_result: str | None, health: ContainerHealth):
    client = MagicMock()
    client.get_software.return_value = [desired]
    adapter = MagicMock()
    adapter.inspect_health.return_value = health
    with (
        patch("edge_agent.software._report_updating"),
        patch("edge_agent.software.wait_until_healthy", return_value=gate_result) as gate,
        patch("runtime.docker.docker_available", return_value=True),
        patch("runtime.docker.DockerAdapter", return_value=adapter),
    ):
        reports = reconcile_software(client, "dev", tmp_path / "artifacts")
    return reports, adapter, gate


HEALTHY = ContainerHealth("healthy")


def test_failed_health_check_rolls_back_to_the_previous_release(tmp_path: Path) -> None:
    [first], _, _ = _reconcile(tmp_path, V1, gate_result=None, health=HEALTHY)
    assert first["sync_status"] == "in_sync"

    [report], adapter, _ = _reconcile(tmp_path, V2, gate_result="HEALTHCHECK failed", health=HEALTHY)
    assert report["sync_status"] == "rolled_back"
    assert report["installed_release_id"] == "rel-1.0"
    assert report["installed_version"] == "1.0"
    assert report["last_error"] == "Rolled back to 1.0: Health check failed: HEALTHCHECK failed"
    images = [call.kwargs["image"] for call in adapter.apply.call_args_list]
    assert images == ["app:2", "app:1"]

    state = json.loads(applied_state_path(tmp_path / "artifacts").read_text())["app"]
    assert state["release_id"] == "rel-1.0"
    assert state["rolled_back_from"]["release_id"] == "rel-2.0"


def test_a_rolled_back_release_is_not_tried_again(tmp_path: Path) -> None:
    _reconcile(tmp_path, V1, gate_result=None, health=HEALTHY)
    _reconcile(tmp_path, V2, gate_result="HEALTHCHECK failed", health=HEALTHY)

    [again], adapter, _ = _reconcile(tmp_path, V2, gate_result=None, health=HEALTHY)
    assert again["sync_status"] == "rolled_back"
    assert again["health"] == "healthy"
    adapter.apply.assert_not_called()

    # A new desired revision (e.g. a fixed release) is tried.
    fixed = _item("2.1", 3, image="app:2.1")
    [report], adapter, _ = _reconcile(tmp_path, fixed, gate_result=None, health=HEALTHY)
    assert report["sync_status"] == "in_sync"
    assert report["installed_release_id"] == "rel-2.1"


def test_apply_failure_rolls_back_too(tmp_path: Path) -> None:
    _reconcile(tmp_path, V1, gate_result=None, health=HEALTHY)
    client = MagicMock()
    client.get_software.return_value = [V2]
    adapter = MagicMock()
    adapter.inspect_health.side_effect = [ContainerHealth("unhealthy", "container app is not running"), HEALTHY]
    with (
        patch("edge_agent.software._report_updating"),
        patch("edge_agent.software.wait_until_healthy", return_value=None),
        patch("runtime.docker.docker_available", return_value=True),
        patch("runtime.docker.DockerAdapter", return_value=adapter),
    ):
        [report] = reconcile_software(client, "dev", tmp_path / "artifacts")
    assert report["sync_status"] == "rolled_back"
    assert "container app is not running after apply." in report["last_error"]


def test_first_install_has_nothing_to_roll_back_to(tmp_path: Path) -> None:
    [report], adapter, _ = _reconcile(tmp_path, V2, gate_result="HEALTHCHECK failed", health=HEALTHY)
    assert report["sync_status"] == "failed"
    assert report["last_error"] == "Health check failed: HEALTHCHECK failed"
    assert adapter.apply.call_count == 1


def test_rollback_can_be_switched_off(tmp_path: Path) -> None:
    _reconcile(tmp_path, V1, gate_result=None, health=HEALTHY)
    no_rollback = _item("2.0", 2, image="app:2", gate={**GATE, "rollback": False})
    [report], adapter, _ = _reconcile(tmp_path, no_rollback, gate_result="HEALTHCHECK failed", health=HEALTHY)
    assert report["sync_status"] == "failed"
    assert adapter.apply.call_count == 1


def test_releases_without_a_health_check_keep_todays_behaviour(tmp_path: Path) -> None:
    _reconcile(tmp_path, _item("1.0", 1, image="app:1", gate=None), gate_result=None, health=HEALTHY)
    [report], adapter, gate = _reconcile(
        tmp_path, _item("2.0", 2, image="app:2", gate=None), gate_result="unused", health=HEALTHY
    )
    assert report["sync_status"] == "in_sync"
    gate.assert_not_called()


@pytest.mark.parametrize("restore_health", [ContainerHealth("unhealthy", "old image gone")])
def test_failed_rollback_reports_both_problems(tmp_path: Path, restore_health: ContainerHealth) -> None:
    _reconcile(tmp_path, V1, gate_result=None, health=HEALTHY)
    client = MagicMock()
    client.get_software.return_value = [V2]
    adapter = MagicMock()
    adapter.inspect_health.side_effect = [HEALTHY, restore_health]
    with (
        patch("edge_agent.software._report_updating"),
        patch("edge_agent.software.wait_until_healthy", return_value="HEALTHCHECK failed"),
        patch("runtime.docker.docker_available", return_value=True),
        patch("runtime.docker.DockerAdapter", return_value=adapter),
    ):
        [report] = reconcile_software(client, "dev", tmp_path / "artifacts")
    assert report["sync_status"] == "failed"
    assert "rolling back to 1.0 also failed" in report["last_error"]
