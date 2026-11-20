"""Reconcile desired software: download → apply → health → report.

Supports systemd, docker, and docker_compose on devices that report those
capabilities (Raspberry Pi OS / Debian with optional Docker Engine).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from edge_agent.app_credentials import AppCredential, AppCredentialError, AppCredentialStore, binding_of
from edge_agent.client import AgentApiError, EdgeClient
from edge_agent.health_gate import RuntimeCheck, gate_settings, wait_until_healthy
from edge_agent.heartbeat import send_heartbeat
from edge_agent.signing import ManifestVerifier, credential_fingerprint


def manifest_verifier(
    client: EdgeClient, device_token: str, state_dir: Path, device_id: str | None
) -> ManifestVerifier | None:
    """The verifier for this device's signed desired software (None without a device id)."""
    if not device_id:
        return None
    return ManifestVerifier(
        state_dir / "signing-keys.json",
        str(device_id),
        lambda: client.get_signing_keys(device_token=device_token),
        credential_fingerprint=credential_fingerprint(device_token),
    )


logger = logging.getLogger("edge_agent")

# Release kinds this agent can apply; reported in heartbeat capabilities["deploy_kinds"].
SUPPORTED_KINDS = ("systemd", "docker", "docker_compose")


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
    reapply: bool = False,
    item: dict[str, Any] | None = None,
    rolled_back_from: dict[str, Any] | None = None,
) -> None:
    state = _load_applied_state(path)
    existing = state.get(workload_key)
    previous = existing if isinstance(existing, dict) else {}
    # Keep revision as a high-water mark so a later write cannot lower it,
    # unless this write records a device-side health rollback.
    if rolled_back_from is None and previous:
        try:
            recorded_revision = int(previous.get("revision") or -1)
        except (TypeError, ValueError):
            recorded_revision = -1
        if recorded_revision > revision:
            revision = recorded_revision
            release_id = str(previous.get("release_id") or release_id)
    state[workload_key] = {"release_id": release_id, "revision": revision}
    # The applied item (spec, artifact) is what a later failed update rolls back to.
    kept_item = item if item is not None else previous.get("item")
    if kept_item is not None:
        state[workload_key]["item"] = kept_item
    if rolled_back_from is not None:
        # This release failed its health check: do not try it again until the desired release changes.
        state[workload_key]["rolled_back_from"] = rolled_back_from
    if reapply:
        # The running workload no longer matches what was applied (its token file was
        # lost). Stays set until an apply succeeds, so a failed apply is tried again.
        state[workload_key]["reapply"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The kept item can hold the release's env values: keep the file private.
    os.chmod(path, 0o600)


def _desired_identity(item: dict[str, Any]) -> tuple[str, int] | None:
    release_id = item.get("release_id")
    if release_id is None or release_id == "":
        return None
    try:
        revision = int(item.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    return str(release_id), revision


def _recorded_revision(state: dict[str, Any], workload_key: str) -> int | None:
    recorded = state.get(workload_key)
    if not isinstance(recorded, dict):
        return None
    try:
        return int(recorded.get("revision") or -1)
    except (TypeError, ValueError):
        return None


def _stale_revision(state: dict[str, Any], item: dict[str, Any]) -> str | None:
    """Refuse a previously signed older revision for the same workload."""
    identity = _desired_identity(item)
    recorded_revision = _recorded_revision(state, str(item.get("workload_key") or ""))
    if identity is None or recorded_revision is None:
        return None
    _, revision = identity
    if revision < recorded_revision:
        return (
            f"Desired revision {revision} is older than applied revision "
            f"{recorded_revision}; refusing rollback."
        )
    return None


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _already_downloaded(path: Path, expected_checksum: str | None) -> bool:
    if not path.is_file():
        return False
    sidecar = checksum_sidecar_path(path)
    if not sidecar.is_file():
        return False
    stored = sidecar.read_text(encoding="utf-8").strip().lower()
    if expected_checksum and stored != expected_checksum.lower():
        return False
    if expected_checksum and _file_sha256(path) != expected_checksum.lower():
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
    expected_sha256: str | None = None,
) -> Path:
    """Download (or reuse) an artifact. ``expected_sha256`` comes from the signed manifest."""
    dest = artifact_cache_path(artifacts_dir, str(artifact_id))
    if _already_downloaded(dest, expected_sha256):
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
    if expected_sha256 and checksum.lower() != expected_sha256.lower():
        dest.unlink(missing_ok=True)
        raise AgentApiError(
            0,
            "checksum_mismatch",
            f"Artifact {artifact_id} does not match the signed checksum; refusing to install it.",
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


def _bound_env(spec: dict[str, Any], app: AppCredential | None) -> dict[str, str]:
    """The release's env plus the METEORCLOUD_* variables; a release may set its own endpoint."""
    env = dict(spec.get("env") or {})
    if app is not None:
        endpoint = env.get("METEORCLOUD_ENDPOINT")
        env.update(app.env)
        if endpoint:
            env["METEORCLOUD_ENDPOINT"] = endpoint
    return env


def _apply_docker(item: dict[str, Any], tarball: Path | None, app: AppCredential | None = None) -> dict[str, Any]:
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
            image_digest=spec.get("image_digest"),
            tarball=tarball,
            ports=list(spec.get("ports") or []),
            env=_bound_env(spec, app),
            restart=spec.get("restart", "unless-stopped"),
            command=spec.get("command"),
            volumes=[app.volume] if app is not None else None,
        )
        health = adapter.inspect_health(container_name=container_name)
        # Not up after install: the install failed. A HEALTHCHECK still starting is fine (unknown).
        if health.status in ("unhealthy", "missing"):
            return _failed(item["workload_key"], f"{health.problem} after apply.")
        return _in_sync_from(item, health)
    except DockerError as exc:
        return _failed(item["workload_key"], exc.message)


def _apply_compose(
    item: dict[str, Any], artifact: Path, work_root: Path, app: AppCredential | None = None,
    force_recreate: bool = False,
) -> dict[str, Any]:
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
            # Only the METEORCLOUD_* variables go to the services; a release may set its
            # own endpoint, as for a docker release. The rest of spec.env stays
            # substitution input for the compose file.
            service_env=_bound_env({"env": {k: v for k, v in (spec.get("env") or {}).items()
                                            if k == "METEORCLOUD_ENDPOINT"}}, app) if app is not None else None,
            service_volumes=[app.volume] if app is not None else None,
            force_recreate=force_recreate,
        )
        health = adapter.inspect_health(project_name=project_name)
        if health.status in ("unhealthy", "missing"):
            return _failed(item["workload_key"], f"{health.problem} after apply.")
        return _in_sync_from(item, health)
    except ComposeError as exc:
        return _failed(item["workload_key"], exc.message)


def _apply_item(
    client: EdgeClient,
    device_token: str,
    item: dict[str, Any],
    *,
    artifacts_dir: Path,
    compose_work: Path,
    app: AppCredential | None,
    was_applied: bool,
) -> dict[str, Any]:
    """Download what the item needs and apply it; return its software_report entry."""
    workload_key = item["workload_key"]
    kind = item.get("kind")
    artifact_id = item.get("artifact_id")
    if kind == "systemd":
        if not artifact_id:
            return _failed(workload_key, "systemd release is missing an artifact_id.")
        unit_file = _ensure_artifact(
            client,
            device_token,
            artifact_id=str(artifact_id),
            workload_key=workload_key,
            artifacts_dir=artifacts_dir,
            expected_sha256=item.get("artifact_sha256"),
        )
        _report_updating(client, device_token, workload_key=workload_key, progress_percent=100)
        return _apply_systemd(item, unit_file)
    if kind == "docker":
        tarball: Path | None = None
        if artifact_id:
            tarball = _ensure_artifact(
                client,
                device_token,
                artifact_id=str(artifact_id),
                workload_key=workload_key,
                artifacts_dir=artifacts_dir,
                expected_sha256=item.get("artifact_sha256"),
            )
        elif not (item.get("spec") or {}).get("image"):
            return _failed(workload_key, "docker release needs an image name or a tarball artifact.")
        _report_updating(client, device_token, workload_key=workload_key, progress_percent=100)
        return _apply_docker(item, tarball, app)
    # docker_compose
    if not artifact_id:
        return _failed(workload_key, "docker_compose release is missing an artifact_id.")
    compose_file = _ensure_artifact(
        client,
        device_token,
        artifact_id=str(artifact_id),
        workload_key=workload_key,
        artifacts_dir=artifacts_dir,
        expected_sha256=item.get("artifact_sha256"),
    )
    _report_updating(client, device_token, workload_key=workload_key, progress_percent=100)
    # Same release applied again with a binding: its token file was lost.
    # The containers must be created again to mount the new file.
    return _apply_compose(item, compose_file, compose_work, app, force_recreate=app is not None and was_applied)


def _runtime_check(item: dict[str, Any], compose_work: Path) -> RuntimeCheck:
    """How to ask the runtime whether this workload is healthy right now."""
    kind = item.get("kind")
    spec = item.get("spec") or {}

    def check() -> tuple[bool | None, str | None]:
        if kind == "systemd":
            from runtime.systemd import SystemdAdapter

            unit_name = str(spec.get("unit_name") or item["workload_key"])
            if not bool(spec.get("start", True)):
                return True, None
            active = SystemdAdapter().health_check(unit_name=unit_name)
            return active, None if active else f"systemd unit {unit_name} is not active"
        if kind == "docker":
            from runtime.docker import DockerAdapter

            container_name = str(spec.get("container_name") or item["workload_key"])
            health = DockerAdapter().inspect_health(container_name=container_name)
        else:
            from runtime.compose import ComposeAdapter

            project_name = str(spec.get("project_name") or item["workload_key"])
            health = ComposeAdapter(work_root=compose_work).inspect_health(project_name=project_name)
        return (None if health.status == "unknown" else health.healthy), health.problem

    return check


def _rolled_back_report(
    item: dict[str, Any], previous: dict[str, Any], *, health: str, reason: str
) -> dict[str, Any]:
    """The previous release runs again because ``item`` failed; the console shows ``reason``."""
    version = previous.get("release_version") or previous.get("release_id")
    return {
        "workload_key": item["workload_key"],
        "installed_release_id": previous.get("release_id"),
        "installed_version": previous.get("release_version"),
        "sync_status": "rolled_back",
        "progress_percent": 100,
        "health": health,
        "last_error": _short(f"Rolled back to {version}: {reason}"),
    }


def _roll_back(
    client: EdgeClient,
    device_token: str,
    item: dict[str, Any],
    *,
    failure: dict[str, Any],
    recorded: Any,
    state_path: Path,
    artifacts_dir: Path,
    compose_work: Path,
    app: AppCredential | None,
) -> dict[str, Any]:
    """Apply the release that ran before ``item``; ``failure`` stands when there is none."""
    previous = recorded.get("item") if isinstance(recorded, dict) else None
    identity = _desired_identity(item)
    reason = str(failure.get("last_error") or "the update failed")
    if not isinstance(previous, dict) or identity is None or _desired_identity(previous) in (None, identity):
        return failure
    logger.warning("%s failed (%s); rolling back to %s", item["workload_key"], reason, previous.get("release_version"))
    try:
        restored = _apply_item(
            client,
            device_token,
            previous,
            artifacts_dir=artifacts_dir,
            compose_work=compose_work,
            app=app if binding_of(previous) is not None else None,
            was_applied=False,
        )
    except Exception as exc:  # noqa: BLE001 — report both problems
        restored = _failed(item["workload_key"], str(getattr(exc, "message", exc)))
    if restored.get("sync_status") != "in_sync":
        return _failed(
            item["workload_key"], f"{reason}; rolling back to {previous.get('release_version')} also failed: "
            f"{restored.get('last_error')}"
        )
    release_id, revision = _desired_identity(previous)  # type: ignore[misc]
    _save_applied_workload(
        state_path,
        workload_key=item["workload_key"],
        release_id=release_id,
        revision=revision,
        item=previous,
        rolled_back_from={"release_id": identity[0], "revision": identity[1], "reason": reason},
    )
    return _rolled_back_report(item, previous, health=str(restored.get("health") or "unknown"), reason=reason)


def _still_rolled_back(
    item: dict[str, Any], recorded: Any, *, compose_work: Path
) -> dict[str, Any] | None:
    """Report for a desired release this device already rolled back from, or None to apply it."""
    if not isinstance(recorded, dict) or not isinstance(recorded.get("rolled_back_from"), dict):
        return None
    failed = recorded["rolled_back_from"]
    identity = _desired_identity(item)
    previous = recorded.get("item")
    if identity is None or not isinstance(previous, dict):
        return None
    if (str(failed.get("release_id")), int(failed.get("revision") or 0)) != identity:
        return None
    current = _health_only(previous, compose_work=compose_work)
    health = str((current or {}).get("health") or "unknown")
    return _rolled_back_report(item, previous, health=health, reason=str(failed.get("reason") or "health check failed"))


def reconcile_software(
    client: EdgeClient,
    device_token: str,
    artifacts_dir: Path,
    verifier: ManifestVerifier | None = None,
) -> list[dict[str, Any]]:
    """Download and apply desired software; return software_report items.

    With a ``verifier``, an item is installed only when its signature verifies.
    """
    try:
        desired = client.get_software(device_token=device_token)
    except AgentApiError as error:
        logger.warning("Could not list desired software (%s)", error.code)
        return []

    compose_work = artifacts_dir.parent / "compose"
    state_path = applied_state_path(artifacts_dir)
    applied = _load_applied_state(state_path)
    credentials = AppCredentialStore(artifacts_dir.parent / "app-credentials")
    reports: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for item in desired:
        workload_key = item.get("workload_key")
        kind = item.get("kind")
        if not workload_key:
            continue

        if kind not in SUPPORTED_KINDS:
            reports.append(_failed(workload_key, f"Unsupported release kind: {kind!r}"))
            continue

        if verifier is not None:
            refused = verifier.check(item)
            if refused is not None:
                logger.warning("Refusing desired software for %s: %s", workload_key, refused)
                reports.append(_failed(workload_key, refused))
                continue

        stale = _stale_revision(applied, item)
        if stale is not None:
            logger.warning("Refusing desired software for %s: %s", workload_key, stale)
            reports.append(_failed(workload_key, stale))
            continue

        accepted.append(item)

    # Prune only after verification so a refused item cannot drop stored credentials.
    credentials.prune(
        {str(item["workload_key"]) for item in accepted if item.get("workload_key") and binding_of(item)}
    )

    for item in accepted:
        workload_key = item["workload_key"]
        kind = item.get("kind")

        rolled_back = _still_rolled_back(item, applied.get(workload_key), compose_work=compose_work)
        if rolled_back is not None:
            reports.append(rolled_back)
            continue

        was_applied = _already_applied(applied, item)
        if was_applied:
            recorded = applied[workload_key]
            if not recorded.get("reapply") and not credentials.keep_fresh(client, device_token, item):
                recorded["reapply"] = True
                release_id, revision = _desired_identity(item)  # not None: it was applied
                _save_applied_workload(
                    state_path, workload_key=workload_key, release_id=release_id, revision=revision, reapply=True
                )
            if not recorded.get("reapply"):
                health_report = _health_only(item, compose_work=compose_work)
                if health_report is not None:
                    reports.append(health_report)
                    continue

        try:
            app = None
            if binding_of(item) is not None:
                if kind == "systemd":
                    reports.append(
                        _failed(
                            workload_key,
                            "A MeteorCloud application binding needs a docker or docker_compose release.",
                        )
                    )
                    continue
                app = credentials.for_apply(client, device_token, item)
                if "METEORCLOUD_ENDPOINT" not in _bound_env(item.get("spec") or {}, app):
                    reports.append(
                        _failed(
                            workload_key,
                            "The control plane has no data plane address (DATA_PLANE_PUBLIC_URL). "
                            "Set it, or set METEORCLOUD_ENDPOINT in the release env.",
                        )
                    )
                    continue
            report = _apply_item(
                client,
                device_token,
                item,
                artifacts_dir=artifacts_dir,
                compose_work=compose_work,
                app=app,
                was_applied=was_applied,
            )
            gate = gate_settings(item.get("spec") or {})
            if gate is not None and report.get("sync_status") == "in_sync":
                reason = wait_until_healthy(
                    _runtime_check(item, compose_work),
                    timeout_seconds=gate["timeout_seconds"],
                    stable_seconds=gate["stable_seconds"],
                    http_url=gate["http_url"],
                )
                report = _in_sync(item, healthy=True) if reason is None else _failed(
                    workload_key, f"Health check failed: {reason}"
                )
            if gate is not None and gate["rollback"] and report.get("sync_status") == "failed":
                report = _roll_back(
                    client,
                    device_token,
                    item,
                    failure=report,
                    recorded=applied.get(workload_key),
                    state_path=state_path,
                    artifacts_dir=artifacts_dir,
                    compose_work=compose_work,
                    app=app,
                )
                if report.get("sync_status") == "rolled_back":
                    applied = _load_applied_state(state_path)
                    reports.append(report)
                    continue

            reports.append(report)
            identity = _desired_identity(item)
            if report.get("sync_status") == "in_sync" and identity is not None:
                release_id, revision = identity
                _save_applied_workload(
                    state_path,
                    workload_key=workload_key,
                    release_id=release_id,
                    revision=revision,
                    item=item,
                )
                applied[workload_key] = {"release_id": release_id, "revision": revision, "item": item}
        except AppCredentialError as error:
            reports.append(_failed(workload_key, error.message))
            logger.warning("No application credentials for %s", workload_key)
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
