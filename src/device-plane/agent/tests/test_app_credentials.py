"""Workloads bound to a MeteorCloud application get a mounted token file and METEORCLOUD_* env."""

from __future__ import annotations

import json
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from runtime.compose import OVERRIDE_FILE, ComposeAdapter
from runtime.docker import ContainerHealth, DockerAdapter

from edge_agent.app_credentials import TOKEN_MOUNT_PATH, AppCredentialStore
from edge_agent.client import AgentApiError
from edge_agent.software import reconcile_software


def _issued(token: str = "mca_first", *, refresh_in: float = 3600, expires_in: float = 7200, endpoint="https://data.x"):
    now = datetime.now(UTC)
    return {
        "token": token,
        "credential_id": "cred-1",
        "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "refresh_after": (now + timedelta(seconds=refresh_in)).isoformat(),
        "endpoint": endpoint,
        "application": "meter",
        "application_id": "app-1",
        "organization_id": "org-1",
        "device_id": "dev-1",
        "environment": "production",
    }


def _item(**spec):
    return {
        "workload_key": "meter-app",
        "kind": "docker",
        "release_id": "rel-1",
        "revision": 1,
        "artifact_id": None,
        "spec": {"container_name": "meter-app", "image": "example/meter:1", "env": {"LOG": "info"},
                 "meteorcloud": {"application": "meter", "environment": "production"}, **spec},
    }


def _reconcile(client, tmp_path: Path, runner):
    with (
        patch("edge_agent.software._report_updating"),
        patch("runtime.docker.docker_available", return_value=True),
        patch("runtime.docker.DockerAdapter", side_effect=lambda: DockerAdapter(runner=runner)),
        patch("runtime.docker.inspect_container_health", return_value=ContainerHealth("healthy")),
    ):
        return reconcile_software(client, "dev_token", tmp_path / "artifacts")


class Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, *, check=True):
        self.calls.append(list(args))
        stdout = "web\nworker\n" if args[-2:] == ["config", "--services"] else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def run_args(self) -> list[str]:
        return next(call for call in self.calls if call[:2] == ["docker", "run"])


def test_bound_docker_workload_gets_token_file_and_env(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [_item()]
    client.mint_app_credential.return_value = _issued()
    runner = Runner()

    reports = _reconcile(client, tmp_path, runner)

    assert reports[0]["sync_status"] == "in_sync"
    client.mint_app_credential.assert_called_once_with(device_token="dev_token", workload_key="meter-app")
    token_path = tmp_path / "app-credentials" / "meter-app" / "token"
    assert token_path.read_text() == "mca_first\n"
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700  # only root reaches it on the host
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o644  # readable by a non-root container user

    args = runner.run_args()
    assert f"{token_path}:{TOKEN_MOUNT_PATH}:ro" in args
    env = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args if "=" in a and a != args[-1]}
    assert env["METEORCLOUD_ENDPOINT"] == "https://data.x"
    assert env["METEORCLOUD_TOKEN_FILE"] == TOKEN_MOUNT_PATH
    assert (env["METEORCLOUD_APP_ID"], env["METEORCLOUD_DEVICE_ID"], env["LOG"]) == ("meter", "dev-1", "info")
    # The token itself is never passed on the command line or in the environment.
    assert not any("mca_first" in arg for call in runner.calls for arg in call)
    assert "mca_first" not in (token_path.parent / "meta.json").read_text()


def test_token_is_rotated_in_place_without_restarting_the_container(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [_item()]
    client.mint_app_credential.return_value = _issued(refresh_in=-1)  # already due
    runner = Runner()
    _reconcile(client, tmp_path, runner)
    token_path = tmp_path / "app-credentials" / "meter-app" / "token"
    inode = token_path.stat().st_ino
    runs = len([c for c in runner.calls if c[:2] == ["docker", "run"]])

    client.mint_app_credential.return_value = _issued("mca_second")
    reports = _reconcile(client, tmp_path, runner)

    assert reports[0]["sync_status"] == "in_sync"
    assert token_path.read_text() == "mca_second\n"
    assert token_path.stat().st_ino == inode  # same file: the bind mount sees the new token
    assert len([c for c in runner.calls if c[:2] == ["docker", "run"]]) == runs

    # Not due: no further minting.
    client.mint_app_credential.reset_mock()
    _reconcile(client, tmp_path, runner)
    client.mint_app_credential.assert_not_called()


def test_rotation_failure_keeps_the_running_workload(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [_item()]
    client.mint_app_credential.return_value = _issued(refresh_in=-1)
    runner = Runner()
    _reconcile(client, tmp_path, runner)
    client.mint_app_credential.side_effect = AgentApiError(0, "network_error", "offline")

    reports = _reconcile(client, tmp_path, runner)

    assert reports[0]["sync_status"] == "in_sync"
    assert (tmp_path / "app-credentials" / "meter-app" / "token").read_text() == "mca_first\n"


def test_no_credentials_means_the_workload_is_not_started(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [_item()]
    client.mint_app_credential.side_effect = AgentApiError(404, "application_not_found", "Application was not found.")
    runner = Runner()

    reports = _reconcile(client, tmp_path, runner)

    assert reports[0]["sync_status"] == "failed"
    assert "Application was not found" in reports[0]["last_error"]
    assert not [c for c in runner.calls if c[:2] == ["docker", "run"]]


def test_missing_endpoint_is_reported_unless_the_release_sets_one(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [_item()]
    client.mint_app_credential.return_value = _issued(endpoint=None)
    runner = Runner()
    reports = _reconcile(client, tmp_path, runner)
    assert reports[0]["sync_status"] == "failed" and "DATA_PLANE_PUBLIC_URL" in reports[0]["last_error"]

    client.get_software.return_value = [_item(env={"METEORCLOUD_ENDPOINT": "https://own.example"})]
    reports = _reconcile(client, tmp_path, runner)
    assert reports[0]["sync_status"] == "in_sync"
    assert "METEORCLOUD_ENDPOINT=https://own.example" in runner.run_args()


def test_lost_token_file_restarts_the_workload_and_unbound_tokens_are_removed(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [_item()]
    client.mint_app_credential.return_value = _issued()
    runner = Runner()
    _reconcile(client, tmp_path, runner)
    token_path = tmp_path / "app-credentials" / "meter-app" / "token"
    token_path.unlink()

    _reconcile(client, tmp_path, runner)
    assert token_path.read_text() == "mca_first\n"
    assert len([c for c in runner.calls if c[:2] == ["docker", "run"]]) == 2

    unbound = _item()
    del unbound["spec"]["meteorcloud"]
    unbound["revision"] = 2
    client.get_software.return_value = [unbound]
    _reconcile(client, tmp_path, runner)
    assert not token_path.parent.exists()
    last_run = [c for c in runner.calls if c[:2] == ["docker", "run"]][-1]
    assert not any("METEORCLOUD" in arg or TOKEN_MOUNT_PATH in arg for arg in last_run)


def test_systemd_release_cannot_be_bound(tmp_path: Path) -> None:
    client = MagicMock()
    item = _item()
    item["kind"] = "systemd"
    client.get_software.return_value = [item]
    reports = _reconcile(client, tmp_path, Runner())
    assert reports[0]["sync_status"] == "failed" and "docker" in reports[0]["last_error"]
    client.mint_app_credential.assert_not_called()


def test_compose_services_get_the_mount_through_an_override_file(tmp_path: Path) -> None:
    artifact = tmp_path / "compose.yaml"
    artifact.write_text("services:\n  web:\n    image: a\n  worker:\n    image: b\n")
    runner = Runner()
    adapter = ComposeAdapter(runner=runner, work_root=tmp_path / "compose")

    adapter.apply(project_name="shop", artifact=artifact, env={"TAG": "1"},
                  service_env={"METEORCLOUD_ENDPOINT": "https://data.x"},
                  service_volumes=[f"/var/lib/x/token:{TOKEN_MOUNT_PATH}:ro"])

    override = tmp_path / "compose" / "shop" / OVERRIDE_FILE
    written = json.loads(override.read_text())
    assert set(written["services"]) == {"web", "worker"}
    assert written["services"]["web"] == {"environment": {"METEORCLOUD_ENDPOINT": "https://data.x"},
                                          "volumes": [f"/var/lib/x/token:{TOKEN_MOUNT_PATH}:ro"]}
    up = runner.calls[-1]
    assert up[-3:] == ["up", "-d", "--remove-orphans"] and str(override) in up
    # The developer's compose file is not modified.
    assert (tmp_path / "compose" / "shop" / "compose.yaml").read_text() == artifact.read_text()

    adapter.apply(project_name="shop", artifact=artifact)
    assert not override.exists() and str(override) not in runner.calls[-1]


def test_store_reuses_a_valid_token_while_offline(tmp_path: Path) -> None:
    store = AppCredentialStore(tmp_path / "creds")
    client = MagicMock()
    client.mint_app_credential.return_value = _issued(refresh_in=-1)
    first = store.for_apply(client, "dev_token", _item())
    client.mint_app_credential.side_effect = AgentApiError(0, "network_error", "offline")
    again = store.for_apply(client, "dev_token", _item())
    assert again == first and again.token_path.read_text() == "mca_first\n"
