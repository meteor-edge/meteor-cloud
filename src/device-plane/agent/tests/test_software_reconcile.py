"""Agent software reconcile: systemd apply + unknown kind safety."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from edge_agent.software import reconcile_software


def test_systemd_reconcile_sets_installed_when_healthy(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [
        {
            "workload_key": "cam.service",
            "kind": "systemd",
            "release_id": "rel-1",
            "release_version": "1.2.0",
            "artifact_id": "art-1",
            "spec": {"unit_name": "cam.service", "start": True},
        }
    ]
    unit_path = tmp_path / "art-1"
    unit_path.write_text("[Unit]\n", encoding="utf-8")
    (tmp_path / "art-1.sha256").write_text("abc\n", encoding="utf-8")

    adapter = MagicMock()
    adapter.health_check.return_value = True

    with (
        patch("edge_agent.software._ensure_artifact", return_value=unit_path),
        patch("edge_agent.software._report_updating"),
        patch("runtime.systemd.systemd_available", return_value=True),
        patch("runtime.systemd.SystemdAdapter", return_value=adapter),
    ):
        reports = reconcile_software(client, "dev_token", tmp_path)

    assert len(reports) == 1
    assert reports[0]["sync_status"] == "in_sync"
    assert reports[0]["health"] == "healthy"
    assert reports[0]["installed_release_id"] == "rel-1"
    adapter.apply.assert_called_once()


def test_systemd_health_fail_does_not_set_installed(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [
        {
            "workload_key": "cam.service",
            "kind": "systemd",
            "release_id": "rel-1",
            "release_version": "1.2.0",
            "artifact_id": "art-1",
            "spec": {"unit_name": "cam.service", "start": True},
        }
    ]
    unit_path = tmp_path / "art-1"
    unit_path.write_text("[Unit]\n", encoding="utf-8")
    adapter = MagicMock()
    adapter.health_check.return_value = False

    with (
        patch("edge_agent.software._ensure_artifact", return_value=unit_path),
        patch("edge_agent.software._report_updating"),
        patch("runtime.systemd.systemd_available", return_value=True),
        patch("runtime.systemd.SystemdAdapter", return_value=adapter),
    ):
        reports = reconcile_software(client, "dev_token", tmp_path)

    assert reports[0]["sync_status"] == "failed"
    assert reports[0]["health"] == "unhealthy"
    assert "installed_release_id" not in reports[0]
    assert "not active" in (reports[0]["last_error"] or "")


def test_unknown_kind_reports_failed_without_raising(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [
        {
            "workload_key": "model.bin",
            "kind": "ai_model",
            "release_id": "rel-9",
            "artifact_id": None,
            "spec": {},
        }
    ]
    reports = reconcile_software(client, "dev_token", tmp_path)
    assert reports[0]["sync_status"] == "failed"
    assert "Unsupported" in (reports[0]["last_error"] or "")


def test_docker_reconcile_sets_installed_when_healthy(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [
        {
            "workload_key": "app",
            "kind": "docker",
            "release_id": "rel-2",
            "release_version": "2.0",
            "artifact_id": None,
            "spec": {"container_name": "app", "image": "busybox:latest"},
        }
    ]
    adapter = MagicMock()
    adapter.health_check.return_value = True

    with (
        patch("edge_agent.software._report_updating"),
        patch("runtime.docker.docker_available", return_value=True),
        patch("runtime.docker.DockerAdapter", return_value=adapter),
    ):
        reports = reconcile_software(client, "dev_token", tmp_path)

    assert reports[0]["sync_status"] == "in_sync"
    assert reports[0]["installed_release_id"] == "rel-2"
    adapter.apply.assert_called_once()


def test_compose_reconcile_sets_installed_when_healthy(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [
        {
            "workload_key": "stack",
            "kind": "docker_compose",
            "release_id": "rel-3",
            "artifact_id": "art-3",
            "spec": {"project_name": "stack"},
        }
    ]
    compose_path = tmp_path / "art-3"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    adapter = MagicMock()
    adapter.health_check.return_value = True

    with (
        patch("edge_agent.software._ensure_artifact", return_value=compose_path),
        patch("edge_agent.software._report_updating"),
        patch("runtime.compose.compose_available", return_value=True),
        patch("runtime.compose.ComposeAdapter", return_value=adapter),
    ):
        reports = reconcile_software(client, "dev_token", tmp_path)

    assert reports[0]["sync_status"] == "in_sync"
    adapter.apply.assert_called_once()
