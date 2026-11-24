"""Device telemetry history: authorized here, served by the data plane (ClickHouse)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.applications.dependencies import get_data_plane_api
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


class _FakeDataPlane:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_device_metrics(self, *, organization_id, device_id, params):
        self.calls.append({"organization_id": organization_id, "device_id": device_id, **params})
        return {
            "start": params["start"],
            "end": params["end"],
            "bucket_seconds": 900,
            "series": [
                {
                    "name": "cpu_percent",
                    "points": [{"t": params["start"], "avg": 12.5, "min": 3.0, "max": 40.0, "count": 15}],
                },
                {"name": "memory_percent", "points": []},
            ],
        }


def _device(client: TestClient, db_session: Session, email: str = "owner@example.com"):
    owner = create_user(db_session, email=email)
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, email)
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap", "max_uses": 5},
    ).json()["token"]
    device = client.post("/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:41"]}).json()
    return org, headers, device["device_id"]


def test_telemetry_is_authorized_and_forwarded(client: TestClient, db_session: Session) -> None:
    org, headers, device_id = _device(client, db_session)
    fake = _FakeDataPlane()
    client.app.dependency_overrides[get_data_plane_api] = lambda: fake
    url = f"/api/v1/organizations/{org.id}/devices/{device_id}/telemetry"

    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range"] == "24h" and body["bucket_seconds"] == 900
    assert [s["name"] for s in body["series"]] == ["cpu_percent", "memory_percent"]
    assert body["series"][0]["points"][0]["avg"] == 12.5
    call = fake.calls[0]
    assert call["organization_id"] == org.id and str(call["device_id"]) == device_id
    assert call["bucket"] == "15m" and call["start"].endswith("Z")
    assert call["names"].split(",")[:4] == ["cpu_percent", "memory_percent", "disk_percent", "temperature_c"]

    assert client.get(url + "?range=1h", headers=headers).status_code == 200
    assert fake.calls[-1]["bucket"] == "1m"
    assert client.get(url + "?range=7d", headers=headers).status_code == 200
    assert fake.calls[-1]["bucket"] == "1h"
    assert client.get(url + "?range=2y", headers=headers).status_code == 422

    # A viewer may read device telemetry; a stranger and another org's device may not.
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    assert client.get(url, headers=auth_header(client, "viewer@example.com")).status_code == 200
    create_user(db_session, email="stranger@example.com")
    calls = len(fake.calls)
    assert client.get(url, headers=auth_header(client, "stranger@example.com")).status_code in (403, 404)
    missing = f"/api/v1/organizations/{org.id}/devices/{uuid.uuid4()}/telemetry"
    assert client.get(missing, headers=headers).status_code == 404
    assert len(fake.calls) == calls, "the data plane must not be asked before authorization"


def test_telemetry_without_data_plane_is_503(client: TestClient, db_session: Session) -> None:
    org, headers, device_id = _device(client, db_session)
    client.app.dependency_overrides[get_data_plane_api] = lambda: None
    response = client.get(f"/api/v1/organizations/{org.id}/devices/{device_id}/telemetry", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "telemetry_unavailable"
