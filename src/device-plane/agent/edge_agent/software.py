"""Reconcile desired software: download → apply → health → report.

Supports systemd, docker, and docker_compose on devices that report those
capabilities (Raspberry Pi OS / Debian with optional Docker Engine).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from edge_agent.client import AgentApiError, EdgeClient
from edge_agent.heartbeat import send_heartbeat

logger = logging.getLogger("edge_agent")


def artifact_cache_path(artifacts_dir: Path, artifact_id: str) -> Path:
    return artifacts_dir / artifact_id


def checksum_sidecar_path(artifact_path: Path) -> Path:
    return artifact_path.with_suffix(artifact_path.suffix + ".sha256")


def applied_state_path(artifacts_dir: Path) -> Path:
    """Persisted release_id/revision per workload (skip re-apply when unchanged)."""
    return artifacts_dir.parent / "applied-software.json"


def _load_applied_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_applied_workload(
    path: Path,
    *,
    workload_key: str,
    release_id: str,
    revision: int,
) -> None:
    state = _load_applied_state(path)
    state[workload_key] = {"release_id": release_id, "revision": revision}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _desired_identity(item: dict[str, Any]) -> tuple[str, int] | None:
    release_id = item.get("release_id")
    if release_id is None or release_id == "":
        return None
    try:
        revision = int(item.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    return str(release_id), revision


def _already_applied(state: dict[str, Any], item: dict[str, Any]) -> bool:
    identity = _desired_identity(item)
    if identity is None:
        return False
    release_id, revision = identity
    recorded = state.get(item["workload_key"])
    if not isinstance(recorded, dict):
        return False
    try:
        recorded_revision = int(recorded.get("revision") or -1)
    except (TypeError, ValueError):
        return False
    return str(recorded.get("release_id") or "") == release_id and recorded_revision == revision


def _health_only(item: dict[str, Any], *, compose_work: Path) -> dict[str, Any] | None:
    """Report runtime health of an already-applied workload without re-applying it.

    A stopped unit or container is reported as in_sync + unhealthy, never re-installed.
    None (so the caller re-applies) when the runtime is missing, or the container or
    compose project no longer exists (e.g. after ``docker rm``).
    """
    kind = item.get("kind")
    spec = item.get("spec") or {}
    try:
        if kind == "systemd":
            from runtime.systemd import SystemdAdapter, systemd_available

            if not systemd_available():
                return None
            unit_name = str(spec.get("unit_name") or item["workload_key"])
            if not bool(spec.get("start", True)):
                return _in_sync(item, healthy=True)
            active = SystemdAdapter().health_check(unit_name=unit_name)
            return _in_sync(item, healthy=active, error=None if active else f"systemd unit {unit_name} is not active")
        if kind == "docker":
            from runtime.docker import DockerAdapter, docker_available

            if not docker_available():
                return None
            container_name = str(spec.get("container_name") or item["workload_key"])
            return _in_sync_from(item, DockerAdapter().inspect_health(container_name=container_name))
        if kind == "docker_compose":
            from runtime.compose import ComposeAdapter, compose_available

            if not compose_available():
                return None
            project_name = str(spec.get("project_name") or item["workload_key"])
            return _in_sync_from(item, ComposeAdapter(work_root=compose_work).inspect_health(project_name=project_name))
    except Exception:  # noqa: BLE001 — fall through to full apply
        logger.debug("Health-only check failed for %s; will re-apply", item.get("workload_key"))
    return None


def _already_downloaded(path: Path, expected_checksum: str | None) -> bool:
    if not path.is_file():
        return False
    sidecar = checksum_sidecar_path(path)
    if not sidecar.is_file():
        return False
    stored = sidecar.read_text(encoding="utf-8").strip().lower()
    if expected_checksum and stored != expected_checksum.lower():
        return False
    return True


def _write_sidecar(path: Path, checksum: str) -> None:
    checksum_sidecar_path(path).write_text(checksum.lower() + "\n", encoding="utf-8")


def _report_updating(
    client: EdgeClient,
    device_token: str,
    *,
    workload_key: str,
    progress_percent: int,
) -> None:
    try:
        send_heartbeat(
            client,
            device_token,
            {
                "software_report": [
                    {
                        "workload_key": workload_key,
                        "sync_status": "updating",
                        "progress_percent": progress_percent,
                        "health": "unknown",
                    }
                ]
            },
        )
    except AgentApiError as error:
        logger.warning("Could not report progress for %s (%s)", workload_key, error.code)


def _ensure_artifact(
    client: EdgeClient,
    device_token: str,
    *,
    artifact_id: str,
    workload_key: str,
    artifacts_dir: Path,
) -> Path:
    dest = artifact_cache_path(artifacts_dir, str(artifact_id))
    if _already_downloaded(dest, None):
        return dest

    _report_updating(client, device_token, workload_key=workload_key, progress_percent=0)
    last_progress_bucket = -1

    def on_progress(received: int, total: int | None) -> None:
        nonlocal last_progress_bucket
        if total and total > 0:
            percent = min(99, int(received * 100 / total))
        else:
            percent = 0
        bucket = percent // 10
        if bucket == last_progress_bucket:
            return
        last_progress_bucket = bucket
        _report_updating(client, device_token, workload_key=workload_key, progress_percent=percent)

    checksum = client.download_artifact(
        device_token=device_token,
        artifact_id=str(artifact_id),
        destination=dest,
        on_progress=on_progress,
    )
    _write_sidecar(dest, checksum)
    logger.info("Downloaded artifact %s for %s", artifact_id, workload_key)
    return dest


# last_error is shown in the console; the API accepts up to 2000 characters.
MAX_ERROR_CHARS = 500


def _short(message: str | None) -> str | None:
    return message[:MAX_ERROR_CHARS] if message else None


def _failed(workload_key: str, message: str) -> dict[str, Any]:
    return {
        "workload_key": workload_key,
        "sync_status": "failed",
        "progress_percent": 0,
        "health": "unhealthy",
        "last_error": _short(message),
    }


def _in_sync(item: dict[str, Any], *, healthy: bool | None, error: str | None = None) -> dict[str, Any]:
    """Installed. ``healthy`` None means not known yet (e.g. a HEALTHCHECK still starting)."""
    return {
        "workload_key": item["workload_key"],
        "installed_release_id": item.get("release_id"),
        "installed_version": item.get("release_version"),
        "sync_status": "in_sync",
        "progress_percent": 100,
        "health": "unknown" if healthy is None else "healthy" if healthy else "unhealthy",
        "last_error": _short(error),
    }


def _in_sync_from(item: dict[str, Any], health: Any) -> dict[str, Any] | None:
    """``_in_sync`` from a runtime ContainerHealth; None when the workload is missing (re-apply)."""
    if health.status == "missing":
        return None
    healthy = None if health.status == "unknown" else health.healthy
    return _in_sync(item, healthy=healthy, error=health.problem)


def _apply_systemd(item: dict[str, Any], unit_file: Path) -> dict[str, Any]:
    from runtime.systemd import SystemdAdapter, SystemdError, systemd_available

    if not systemd_available():
        return _failed(item["workload_key"], "This device does not support systemd.")

    spec = item.get("spec") or {}
    unit_name = str(spec.get("unit_name") or item["workload_key"])
    start = bool(spec.get("start", True))
    adapter = SystemdAdapter()
    try:
        adapter.apply(unit_name=unit_name, unit_file=unit_file, start=start)
        if start and not adapter.health_check(unit_name=unit_name):
            return _failed(item["workload_key"], f"Unit {unit_name} is not active after apply.")
        return _in_sync(item, healthy=True)
    except SystemdError as exc:
        return _failed(item["workload_key"], exc.message)


def _apply_docker(item: dict[str, Any], tarball: Path | None) -> dict[str, Any]:
    from runtime.docker import DockerAdapter, DockerError, docker_available

    if not docker_available():
        return _failed(item["workload_key"], "This device does not support Docker.")

    spec = item.get("spec") or {}
    container_name = str(spec.get("container_name") or item["workload_key"])
    adapter = DockerAdapter()
    try:
        adapter.apply(
            container_name=container_name,
            image=spec.get("image"),
            tarball=tarball,
            ports=list(spec.get("ports") or []),
            env=dict(spec.get("env") or {}),
            restart=spec.get("restart", "unless-stopped"),
            command=spec.get("command"),
        )
        health = adapter.inspect_health(container_name=container_name)
        # Not up after install: the install failed. A HEALTHCHECK still starting is fine (unknown).
        if health.status in ("unhealthy", "missing"):
            return _failed(item["workload_key"], f"{health.problem} after apply.")
        return _in_sync_from(item, health)
    except DockerError as exc:
        return _failed(item["workload_key"], exc.message)


def _apply_compose(item: dict[str, Any], artifact: Path, work_root: Path) -> dict[str, Any]:
    from runtime.compose import ComposeAdapter, ComposeError, compose_available

    if not compose_available():
        return _failed(
            item["workload_key"],
            "This device does not support Docker Compose.",
        )

    spec = item.get("spec") or {}
    project_name = str(spec.get("project_name") or item["workload_key"])
    adapter = ComposeAdapter(work_root=work_root)
    try:
        adapter.apply(
            project_name=project_name,
            artifact=artifact,
            env=dict(spec.get("env") or {}),
        )
        health = adapter.inspect_health(project_name=project_name)
        if health.status in ("unhealthy", "missing"):
            return _failed(item["workload_key"], f"{health.problem} after apply.")
        return _in_sync_from(item, health)
    except ComposeError as exc:
        return _failed(item["workload_key"], exc.message)


def reconcile_software(
    client: EdgeClient,
    device_token: str,
    artifacts_dir: Path,
) -> list[dict[str, Any]]:
    """Download and apply desired software; return software_report items."""
    try:
        desired = client.get_software(device_token=device_token)
    except AgentApiError as error:
        logger.warning("Could not list desired software (%s)", error.code)
        return []

    compose_work = artifacts_dir.parent / "compose"
    state_path = applied_state_path(artifacts_dir)
    applied = _load_applied_state(state_path)
    reports: list[dict[str, Any]] = []
    for item in desired:
        workload_key = item.get("workload_key")
        kind = item.get("kind")
        if not workload_key:
            continue

        if kind not in {"systemd", "docker", "docker_compose"}:
            reports.append(_failed(workload_key, f"Unsupported release kind: {kind!r}"))
            continue

        if _already_applied(applied, item):
            health_report = _health_only(item, compose_work=compose_work)
            if health_report is not None:
                reports.append(health_report)
                continue

        artifact_id = item.get("artifact_id")

        try:
            if kind == "systemd":
                if not artifact_id:
                    reports.append(_failed(workload_key, "systemd release is missing an artifact_id."))
                    continue
                unit_file = _ensure_artifact(
                    client,
                    device_token,
                    artifact_id=str(artifact_id),
                    workload_key=workload_key,
                    artifacts_dir=artifacts_dir,
                )
                _report_updating(client, device_token, workload_key=workload_key, progress_percent=100)
                report = _apply_systemd(item, unit_file)
            elif kind == "docker":
                tarball: Path | None = None
                if artifact_id:
                    tarball = _ensure_artifact(
                        client,
                        device_token,
                        artifact_id=str(artifact_id),
                        workload_key=workload_key,
                        artifacts_dir=artifacts_dir,
                    )
                elif not (item.get("spec") or {}).get("image"):
                    reports.append(
                        _failed(
                            workload_key,
                            "docker release needs an image name or a tarball artifact.",
                        )
                    )
                    continue
                _report_updating(client, device_token, workload_key=workload_key, progress_percent=100)
                report = _apply_docker(item, tarball)
            else:  # docker_compose
                if not artifact_id:
                    reports.append(
                        _failed(workload_key, "docker_compose release is missing an artifact_id.")
                    )
                    continue
                compose_file = _ensure_artifact(
                    client,
                    device_token,
                    artifact_id=str(artifact_id),
                    workload_key=workload_key,
                    artifacts_dir=artifacts_dir,
                )
                _report_updating(client, device_token, workload_key=workload_key, progress_percent=100)
                report = _apply_compose(item, compose_file, compose_work)

            reports.append(report)
            identity = _desired_identity(item)
            if report.get("sync_status") == "in_sync" and identity is not None:
                release_id, revision = identity
                _save_applied_workload(
                    state_path,
                    workload_key=workload_key,
                    release_id=release_id,
                    revision=revision,
                )
                applied[workload_key] = {"release_id": release_id, "revision": revision}
        except AgentApiError as error:
            reports.append(_failed(workload_key, error.message))
            logger.warning("Software reconcile failed for %s: %s", workload_key, error.code)
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            reports.append(_failed(workload_key, str(exc)))
            logger.exception("Unexpected error reconciling %s", workload_key)

    return reports


# Backwards-compatible name used by earlier Phase 4 call sites/tests.
def reconcile_downloads(
    client: EdgeClient,
    device_token: str,
    artifacts_dir: Path,
) -> list[dict[str, Any]]:
    return reconcile_software(client, device_token, artifacts_dir)
