"""MQTT, device command and Deploy metrics."""

from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.deployments.models import Deployment
from app.deployments.service import DeploymentService
from tests.metrics_helpers import sample
from tests.test_deployments import _register_device, _setup_release

INTERNAL = {"X-MQTT-Internal-Token": get_settings().mqtt_internal_token}


def _report(client: TestClient, reg: dict, release: dict, *, sync_status: str) -> None:
    response = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        json={
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "installed_release_id": release["id"],
                    "installed_version": release["version"],
                    "health": "healthy",
                    "sync_status": sync_status,
                    "progress_percent": 100,
                }
            ]
        },
    )
    assert response.status_code == 200, response.text


def test_mqtt_auth_decisions_are_counted(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    allow_before = sample("meteorcloud_mqtt_auth_total", action="authenticate", result="allow")
    deny_before = sample("meteorcloud_mqtt_auth_total", action="authenticate", result="deny")
    authz_deny_before = sample("meteorcloud_mqtt_auth_total", action="authorize", result="deny")

    client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={"username": reg["mqtt"]["username"], "password": reg["mqtt"]["password"]},
    )
    client.post(
        "/internal/mqtt/authenticate",
        headers=INTERNAL,
        json={"username": reg["mqtt"]["username"], "password": "wrong"},
    )
    client.post(
        "/internal/mqtt/authorize",
        headers=INTERNAL,
        json={"username": reg["mqtt"]["username"], "action": "publish", "topic": f"devices/{uuid.uuid4()}/status"},
    )

    assert sample("meteorcloud_mqtt_auth_total", action="authenticate", result="allow") - allow_before == 1
    assert sample("meteorcloud_mqtt_auth_total", action="authenticate", result="deny") - deny_before == 1
    assert sample("meteorcloud_mqtt_auth_total", action="authorize", result="deny") - authz_deny_before == 1


def test_mqtt_ingest_results_are_counted(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    applied_before = sample("meteorcloud_mqtt_messages_ingested_total", kind="status", result="applied")
    rejected_before = sample("meteorcloud_mqtt_messages_ingested_total", kind="metrics", result="rejected")
    unknown_before = sample("meteorcloud_mqtt_messages_ingested_total", kind="unknown", result="rejected")

    for topic, payload in (
        (f"devices/{reg['device_id']}/status", json.dumps({"status": "online"})),
        (f"devices/{reg['device_id']}/metrics", "not json"),
        ("something/else", "{}"),
    ):
        response = client.post("/internal/mqtt/ingest", headers=INTERNAL, json={"topic": topic, "payload": payload})
        assert response.status_code == 200, response.text

    assert sample("meteorcloud_mqtt_messages_ingested_total", kind="status", result="applied") - applied_before == 1
    assert sample("meteorcloud_mqtt_messages_ingested_total", kind="metrics", result="rejected") - rejected_before == 1
    assert sample("meteorcloud_mqtt_messages_ingested_total", kind="unknown", result="rejected") - unknown_before == 1


def test_deploy_created_and_completed_are_counted(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    created_before = sample("meteorcloud_deployments_created_total", strategy="immediate")
    completed_before = sample("meteorcloud_deployments_finished_total", status="completed")
    succeeded_before = sample("meteorcloud_deployment_device_results_total", status="succeeded")

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    )
    assert created.status_code == 201, created.text
    _report(client, reg, release, sync_status="in_sync")
    _report(client, reg, release, sync_status="in_sync")  # repeated report: no double count

    assert sample("meteorcloud_deployments_created_total", strategy="immediate") - created_before == 1
    assert sample("meteorcloud_deployments_finished_total", status="completed") - completed_before == 1
    assert sample("meteorcloud_deployment_device_results_total", status="succeeded") - succeeded_before == 1


def test_failed_deploy_is_counted_once(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    failed_before = sample("meteorcloud_deployments_finished_total", status="failed")
    device_failed_before = sample("meteorcloud_deployment_device_results_total", status="failed")

    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    ).json()
    _report(client, reg, release, sync_status="failed")

    deployment = db_session.get(Deployment, uuid.UUID(created["id"]))
    assert deployment is not None and deployment.status == "failed"
    service = DeploymentService(db_session)
    service.refresh_deployment_status(deployment)
    service.refresh_deployment_status(deployment)

    assert sample("meteorcloud_deployments_finished_total", status="failed") - failed_before == 1
    assert sample("meteorcloud_deployment_device_results_total", status="failed") - device_failed_before == 1


def test_cancelled_deploy_is_counted(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:01")
    cancelled_before = sample("meteorcloud_deployments_finished_total", status="cancelled")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    ).json()

    response = client.post(f"/api/v1/organizations/{org.id}/deployments/{created['id']}/cancel", headers=headers)
    assert response.status_code == 200, response.text

    assert sample("meteorcloud_deployments_finished_total", status="cancelled") - cancelled_before == 1


def test_ping_commands_are_counted(db_session: Session) -> None:
    from tests.test_mqtt_commands import CompletingPublisher, _client_with_publisher
    from tests.test_mqtt_commands import _register as register_with_mqtt

    client = _client_with_publisher(db_session, CompletingPublisher(db_session))
    headers, body = register_with_mqtt(client, db_session)
    sent_before = sample("meteorcloud_device_commands_total", kind="ping", result="sent")

    response = client.post(
        f"/api/v1/organizations/{body['organization_id']}/devices/{body['device_id']}/commands/ping",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert sample("meteorcloud_device_commands_total", kind="ping", result="sent") - sent_before == 1
