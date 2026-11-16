"""Signed desired-software manifests and organization signing keys."""

from __future__ import annotations

import base64
import hashlib
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.deployments.software_service import DeviceSoftwareService
from app.releases.repository import ReleaseRepository
from app.signing.service import DESIRED_CONTEXT, canonical_json, endorsement_payload
from tests.conftest import auth_header, create_org_with_owner, create_user

UNIT = b"[Unit]\nDescription=Camera\n[Service]\nExecStart=/bin/true\n"


def _b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verify(public_key: str, signature: str, message: bytes) -> None:
    Ed25519PublicKey.from_public_bytes(_b64(public_key)).verify(_b64(signature), message)


def _payload(device_id: str, item: dict) -> dict:
    return {
        "device_id": device_id,
        "workload_key": item["workload_key"],
        "kind": item["kind"],
        "release_id": item["release_id"],
        "release_version": item["release_version"],
        "artifact_id": item["artifact_id"],
        "artifact_sha256": item["artifact_sha256"],
        "revision": item["revision"],
        "spec": item["spec"],
    }


def _setup(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": "Bootstrap"}
    ).json()["token"]
    registration = client.post(
        "/api/v1/agent/register", json={"token": token, "mac_addresses": ["aa:bb:cc:dd:ee:01"]}
    ).json()
    artifact = client.post(
        f"/api/v1/organizations/{org.id}/artifacts",
        headers=headers,
        data={"name": "camera-agent", "version": "1.0", "type": "systemd"},
        files={"file": ("camera-agent.service", UNIT, "text/plain")},
    ).json()
    release = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "camera-agent",
            "version": "1.2.0",
            "kind": "systemd",
            "artifact_id": artifact["id"],
            "spec": {"unit_name": "camera-agent.service", "note": "Grüße"},
        },
    ).json()
    release_row = ReleaseRepository(db_session).get(organization_id=org.id, release_id=uuid.UUID(release["id"]))
    DeviceSoftwareService(db_session).set_desired_software(
        organization_id=org.id, device_id=uuid.UUID(registration["device_id"]), release=release_row
    )
    device_headers = {"Authorization": f"Bearer {registration['device_token']}"}
    return org, headers, registration, device_headers


def test_desired_software_is_signed_with_the_organization_key(client: TestClient, db_session: Session) -> None:
    _, _, registration, device_headers = _setup(client, db_session)

    keys = client.get("/api/v1/agent/signing-keys", headers=device_headers)
    assert keys.status_code == 200, keys.text
    [key] = keys.json()
    assert key["algorithm"] == "ed25519"
    assert key["retired_at"] is None
    assert key["key_id"] == hashlib.sha256(_b64(key["public_key"])).hexdigest()[:16]

    [item] = client.get("/api/v1/agent/software", headers=device_headers).json()
    assert item["key_id"] == key["key_id"]
    assert item["artifact_sha256"] == hashlib.sha256(UNIT).hexdigest()
    message = DESIRED_CONTEXT + canonical_json(_payload(registration["device_id"], item))
    _verify(key["public_key"], item["signature"], message)

    # The signature binds the device and the revision: changing either breaks it.
    for change in ({"device_id": str(uuid.uuid4())}, {"revision": item["revision"] - 1}):
        tampered = {**_payload(registration["device_id"], item), **change}
        try:
            _verify(key["public_key"], item["signature"], DESIRED_CONTEXT + canonical_json(tampered))
        except InvalidSignature:
            pass
        else:
            raise AssertionError(f"signature still verified after {change}")


def test_rotation_endorses_the_new_key_with_the_old_one(client: TestClient, db_session: Session) -> None:
    org, headers, registration, device_headers = _setup(client, db_session)
    [old] = client.get(f"/api/v1/organizations/{org.id}/signing-keys", headers=headers).json()

    rotated = client.post(f"/api/v1/organizations/{org.id}/signing-keys/rotate", headers=headers)
    assert rotated.status_code == 201, rotated.text
    new = rotated.json()
    assert new["key_id"] != old["key_id"]
    assert new["endorsed_by"] == old["key_id"]
    _verify(old["public_key"], new["endorsement"], endorsement_payload(new["key_id"], new["public_key"]))

    keys = {k["key_id"]: k for k in client.get("/api/v1/agent/signing-keys", headers=device_headers).json()}
    assert keys[old["key_id"]]["retired_at"] is not None
    assert keys[new["key_id"]]["retired_at"] is None

    [item] = client.get("/api/v1/agent/software", headers=device_headers).json()
    assert item["key_id"] == new["key_id"]
    message = DESIRED_CONTEXT + canonical_json(_payload(registration["device_id"], item))
    _verify(new["public_key"], item["signature"], message)

    events = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "signing_key.rotated")).all()
    assert len(events) == 1


def test_rotation_needs_organization_update(client: TestClient, db_session: Session) -> None:
    org, _, _, _ = _setup(client, db_session)
    create_user(db_session, email="outsider@example.com")
    outsider = auth_header(client, "outsider@example.com")
    response = client.post(f"/api/v1/organizations/{org.id}/signing-keys/rotate", headers=outsider)
    assert response.status_code in (403, 404)
