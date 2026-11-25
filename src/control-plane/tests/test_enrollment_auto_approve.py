"""Auto-approve rules on enrollment keys and bulk review of enrollment requests (#72)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.devices.models import EnrollmentApiKey
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user


def _setup(client: TestClient, db_session: Session):
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    return org, auth_header(client, "owner@example.com")


def _create_key(client: TestClient, org_id, headers, **extra) -> dict:
    response = client.post(
        f"/api/v1/organizations/{org_id}/enrollment-keys",
        headers=headers,
        json={"name": "Factory batch", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _enroll(client: TestClient, api_key: str, **inventory) -> dict:
    response = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {api_key}"},
        json=inventory,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _poll(client: TestClient, submitted: dict) -> dict:
    response = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": submitted["request_id"], "claim_secret": submitted["claim_secret"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _type_and_group(client: TestClient, org_id, headers) -> tuple[str, str]:
    device_type = client.post(
        f"/api/v1/organizations/{org_id}/device-types",
        headers=headers,
        json={"name": "Raspberry Pi 5", "manufacturer": "Raspberry Pi Ltd", "architecture": "arm64"},
    )
    assert device_type.status_code == 201, device_type.text
    group = client.post(f"/api/v1/organizations/{org_id}/device-groups", headers=headers, json={"name": "Site A"})
    assert group.status_code == 201, group.text
    return device_type.json()["id"], group.json()["id"]


def test_allowlisted_device_is_approved_on_submit_and_claims_at_once(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    type_id, group_id = _type_and_group(client, org.id, headers)
    key = _create_key(
        client,
        org.id,
        headers,
        auto_approve={
            "device_type_id": type_id,
            "device_group_id": group_id,
            "allowlist": ["SN-0001", "AA-BB-CC-DD-EE-02", " ", "SN-0001"],
        },
    )
    rule = key["auto_approve_rule"]
    assert rule["allowlist"] == ["SN-0001", "aa:bb:cc:dd:ee:02"]
    assert rule["approved_count"] == 0

    by_serial = _enroll(client, key["api_key"], serial_number="SN-0001", mac_addresses=["aa:bb:cc:dd:ee:01"])
    assert by_serial["status"] == "approved"
    by_mac = _enroll(client, key["api_key"], serial_number="SN-0002", mac_addresses=["AA:BB:CC:DD:EE:02"])
    assert by_mac["status"] == "approved"
    stranger = _enroll(client, key["api_key"], serial_number="SN-9999", mac_addresses=["aa:bb:cc:dd:ee:99"])
    assert stranger["status"] == "pending"

    claimed = _poll(client, by_serial)
    assert claimed["device_token"].startswith("dev_")
    device = client.get(f"/api/v1/organizations/{org.id}/devices/{claimed['device_id']}", headers=headers).json()
    assert device["device_type_id"] == type_id
    assert device["device_group_id"] == group_id

    listed = client.get(f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers).json()
    by_id = {row["id"]: row for row in listed}
    assert by_id[by_serial["request_id"]]["auto_approved"] is True
    assert by_id[by_serial["request_id"]]["reviewed_by_user_id"] is None
    assert by_id[stranger["request_id"]]["auto_approved"] is False

    keys = client.get(f"/api/v1/organizations/{org.id}/enrollment-keys", headers=headers).json()
    assert keys[0]["auto_approve_rule"]["approved_count"] == 2

    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "enrollment_request.auto_approve")).all()
    assert len(audit) == 2
    assert {row.actor_type for row in audit} == {"enrollment_key"}


def test_rule_stops_at_its_maximum(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers, auto_approve={"max_devices": 2})
    results = [
        _enroll(client, key["api_key"], mac_addresses=[f"aa:bb:cc:dd:ee:0{index}"])["status"] for index in range(1, 4)
    ]
    assert results == ["approved", "approved", "pending"]


def test_retry_of_an_auto_approved_device_does_not_use_another_slot(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers, auto_approve={"max_devices": 1})
    first = _enroll(client, key["api_key"], serial_number="SN-1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    again = _enroll(client, key["api_key"], serial_number="SN-1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    assert first["request_id"] == again["request_id"]
    assert again["status"] == "approved"
    stored = db_session.get(EnrollmentApiKey, key["id"])
    db_session.refresh(stored)
    assert stored.auto_approve_count == 1


def test_rule_past_its_end_time_leaves_requests_pending(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers, auto_approve={"max_devices": 5})
    stored = db_session.get(EnrollmentApiKey, key["id"])
    stored.auto_approve_until = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])["status"] == "pending"


def test_known_hardware_always_waits_for_a_person(client: TestClient, db_session: Session) -> None:
    """Claiming would replace an existing device's credential, so a rule never does that alone."""
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers, auto_approve={"max_devices": 10})
    first = _enroll(client, key["api_key"], serial_number="SN-1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    assert _poll(client, first)["device_token"]

    again = _enroll(client, key["api_key"], serial_number="SN-1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    assert again["status"] == "pending"


def test_device_without_identity_is_not_auto_approved(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers, auto_approve={"max_devices": 10})
    assert _enroll(client, key["api_key"], hostname="no-ids")["status"] == "pending"


def test_rule_needs_an_allowlist_or_a_maximum(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Open", "auto_approve": {}},
    )
    assert response.status_code == 422


def test_rule_with_a_past_end_time_or_foreign_group_is_refused(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Late", "auto_approve": {"max_devices": 1, "until": past}},
    )
    assert response.status_code == 409

    other_owner = create_user(db_session, email="other@example.com")
    other_org, _ = create_org_with_owner(db_session, other_owner, name="Other", slug="other")
    _, other_group = _type_and_group(client, other_org.id, auth_header(client, "other@example.com"))
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys",
        headers=headers,
        json={"name": "Foreign", "auto_approve": {"max_devices": 1, "device_group_id": other_group}},
    )
    assert response.status_code == 404


def test_rule_can_be_turned_on_and_off_later(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers)
    assert key["auto_approve_rule"] is None
    base = f"/api/v1/organizations/{org.id}/enrollment-keys/{key['id']}/auto-approve"

    turned_on = client.put(base, headers=headers, json={"allowlist": ["SN-1"]})
    assert turned_on.status_code == 200, turned_on.text
    assert turned_on.json()["auto_approve_rule"]["allowlist"] == ["SN-1"]
    assert _enroll(client, key["api_key"], serial_number="SN-1")["status"] == "approved"

    turned_off = client.delete(base, headers=headers)
    assert turned_off.status_code == 200
    assert turned_off.json()["auto_approve_rule"] is None
    assert _enroll(client, key["api_key"], serial_number="SN-2")["status"] == "pending"

    actions = set(db_session.scalars(select(AuditEvent.action)).all())
    assert {"enrollment_key.auto_approve", "enrollment_key.auto_approve_off"} <= actions


def test_operator_cannot_set_a_rule(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers)
    operator = create_user(db_session, email="op@example.com")
    add_member(db_session, org, operator, OrganizationRole.OPERATOR)
    response = client.put(
        f"/api/v1/organizations/{org.id}/enrollment-keys/{key['id']}/auto-approve",
        headers=auth_header(client, "op@example.com"),
        json={"max_devices": 1},
    )
    assert response.status_code == 403


def test_full_pending_cap_does_not_block_an_auto_approved_device(client: TestClient, db_session: Session) -> None:
    from app.core.config import get_settings

    capped = get_settings().model_copy(update={"enrollment_max_pending_per_key": 1})
    client.app.dependency_overrides[get_settings] = lambda: capped
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers)
    _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    blocked = client.post(
        "/api/v1/agent/enroll/request",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={"serial_number": "SN-OK"},
    )
    assert blocked.status_code == 429
    client.put(
        f"/api/v1/organizations/{org.id}/enrollment-keys/{key['id']}/auto-approve",
        headers=headers,
        json={"allowlist": ["SN-OK"]},
    )
    assert _enroll(client, key["api_key"], serial_number="SN-OK")["status"] == "approved"


# ----------------------------------------------------------------- bulk review
def test_bulk_approve_assigns_type_group_and_unique_names(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    type_id, group_id = _type_and_group(client, org.id, headers)
    key = _create_key(client, org.id, headers)
    submitted = [
        _enroll(client, key["api_key"], hostname="raspberrypi", mac_addresses=[f"aa:bb:cc:dd:ee:0{index}"])
        for index in range(1, 4)
    ]
    rejected = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:09"])
    client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{rejected['request_id']}/reject",
        headers=headers,
        json={},
    )
    ids = [row["request_id"] for row in submitted]
    missing = "00000000-0000-0000-0000-000000000000"

    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/approve",
        headers=headers,
        json={
            "request_ids": [*ids, ids[0], rejected["request_id"], missing],
            "device_type_id": type_id,
            "device_group_id": group_id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["updated"]] == ids
    assert {row["status"] for row in body["updated"]} == {"approved"}
    assert {row["device_type_id"] for row in body["updated"]} == {type_id}
    names = [row["assigned_name"] for row in body["updated"]]
    assert all(names) and len(set(names)) == 3
    assert body["skipped"] == [
        {"request_id": rejected["request_id"], "reason": "not_pending"},
        {"request_id": missing, "reason": "not_found"},
    ]

    claimed = _poll(client, submitted[1])
    assert claimed["name"] == names[1]


def test_bulk_reject(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers)
    submitted = [_enroll(client, key["api_key"], mac_addresses=[f"aa:bb:cc:dd:ee:0{i}"]) for i in range(1, 3)]
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/reject",
        headers=headers,
        json={"request_ids": [row["request_id"] for row in submitted], "reason": "Wrong site"},
    )
    assert response.status_code == 200, response.text
    assert {row["status"] for row in response.json()["updated"]} == {"rejected"}
    assert _poll(client, submitted[0])["rejection_reason"] == "Wrong site"


def test_bulk_review_is_scoped_to_the_organization(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers)
    submitted = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])

    other_owner = create_user(db_session, email="other@example.com")
    other_org, _ = create_org_with_owner(db_session, other_owner, name="Other", slug="other")
    response = client.post(
        f"/api/v1/organizations/{other_org.id}/enrollment-requests/approve",
        headers=auth_header(client, "other@example.com"),
        json={"request_ids": [submitted["request_id"]]},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == []
    assert response.json()["skipped"] == [{"request_id": submitted["request_id"], "reason": "not_found"}]
    assert _poll(client, submitted)["status"] == "pending"


def test_developer_cannot_bulk_approve(client: TestClient, db_session: Session) -> None:
    org, headers = _setup(client, db_session)
    key = _create_key(client, org.id, headers)
    submitted = _enroll(client, key["api_key"], mac_addresses=["aa:bb:cc:dd:ee:01"])
    developer = create_user(db_session, email="dev@example.com")
    add_member(db_session, org, developer, OrganizationRole.DEVELOPER)
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/approve",
        headers=auth_header(client, "dev@example.com"),
        json={"request_ids": [submitted["request_id"]]},
    )
    assert response.status_code == 403
