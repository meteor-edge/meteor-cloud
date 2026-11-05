"""Default device names: enrolling a device shouldn't require inventing a name."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.models import Device
from app.devices.naming import default_device_name, unique_device_name
from tests.conftest import auth_header, create_org_with_owner, create_user
from tests.test_deployments import _register_device, _setup_release


@pytest.mark.parametrize(
    ("requested", "hostname", "serial", "macs", "expected"),
    [
        ("edge-01", "kiosk", "S1", ["aa:bb:cc:dd:a1:b2"], "edge-01"),
        ("  edge-01 ", None, None, [], "edge-01"),
        (None, "kiosk-lobby", "S1", ["aa:bb:cc:dd:a1:b2"], "kiosk-lobby"),
        (None, "raspberrypi", "S1", ["aa:bb:cc:dd:a1:b2"], "raspberrypi-a1b2"),
        (None, "RaspberryPi.local", None, ["aa:bb:cc:dd:a1:b2"], "raspberrypi-a1b2"),
        (None, "raspberrypi", "10000000c3d4", [], "raspberrypi-c3d4"),
        (None, "raspberrypi", None, [], "raspberrypi"),
        (None, "localhost", None, ["aa:bb:cc:dd:a1:b2"], "device-a1b2"),
        (None, None, "SN-00-77", [], "device-0077"),
        (None, "", None, [], "device"),
    ],
)
def test_default_name(
    requested: str | None, hostname: str | None, serial: str | None, macs: list[str], expected: str
) -> None:
    name = default_device_name(requested_name=requested, hostname=hostname, serial_number=serial, mac_addresses=macs)
    assert name == expected


def test_unique_name_adds_a_number_ignoring_case() -> None:
    assert unique_device_name("Kiosk", taken=set()) == "Kiosk"
    assert unique_device_name("Kiosk", taken={"kiosk"}) == "Kiosk-2"
    assert unique_device_name("kiosk", taken={"kiosk", "kiosk-2"}) == "kiosk-3"
    long_name = "x" * 255
    assert unique_device_name(long_name, taken={long_name}) == "x" * 253 + "-2"


def _org_with_key(client: TestClient, db_session: Session, email: str = "owner@example.com"):
    owner = create_user(db_session, email=email)
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, email)
    key = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys", headers=headers, json={"name": "Field"}
    ).json()["api_key"]
    return org, headers, key


def _submit(client: TestClient, key: str, **body) -> dict:
    response = client.post("/api/v1/agent/enroll/request", headers={"Authorization": f"Bearer {key}"}, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


def _suggestions(client: TestClient, org, headers) -> dict[str, str | None]:
    rows = client.get(f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers).json()
    return {row["id"]: row["suggested_name"] for row in rows}


def _claim(client: TestClient, submitted: dict) -> dict:
    response = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": submitted["request_id"], "claim_secret": submitted["claim_secret"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve(client: TestClient, org, headers, request_id: str, **body) -> None:
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{request_id}/approve", headers=headers, json=body
    )
    assert response.status_code == 200, response.text


def test_two_default_hostnames_get_distinct_suggestions_that_the_device_receives(
    client: TestClient, db_session: Session
) -> None:
    org, headers, key = _org_with_key(client, db_session)
    first = _submit(client, key, hostname="raspberrypi", mac_addresses=["aa:bb:cc:dd:00:01"])
    second = _submit(client, key, hostname="raspberrypi", mac_addresses=["aa:bb:cc:dd:00:02"])
    suggestions = _suggestions(client, org, headers)
    assert suggestions[first["request_id"]] == "raspberrypi-0001"
    assert suggestions[second["request_id"]] == "raspberrypi-0002"

    # Approved without a name: the device still gets the default.
    _approve(client, org, headers, first["request_id"])
    assert _claim(client, first)["name"] == "raspberrypi-0001"
    # Once claimed, a request no longer has a suggestion.
    assert _suggestions(client, org, headers)[first["request_id"]] is None


def test_repeated_hostname_is_numbered_while_pending_and_after_claim(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    first = _submit(client, key, hostname="kiosk", mac_addresses=["aa:bb:cc:dd:00:03"])
    second = _submit(client, key, hostname="kiosk", mac_addresses=["aa:bb:cc:dd:00:04"])
    suggestions = _suggestions(client, org, headers)
    assert suggestions[first["request_id"]] == "kiosk"
    assert suggestions[second["request_id"]] == "kiosk-2"

    _approve(client, org, headers, first["request_id"])
    _approve(client, org, headers, second["request_id"])
    assert _claim(client, first)["name"] == "kiosk"
    assert _claim(client, second)["name"] == "kiosk-2"


def test_typed_name_is_used_as_is_even_if_repeated(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    for mac in ("aa:bb:cc:dd:00:05", "aa:bb:cc:dd:00:06"):
        submitted = _submit(client, key, hostname="kiosk", mac_addresses=[mac])
        _approve(client, org, headers, submitted["request_id"], name="Lobby")
        assert _claim(client, submitted)["name"] == "Lobby"


def test_reenrolling_device_keeps_its_name(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    mac = "aa:bb:cc:dd:00:07"
    first = _submit(client, key, hostname="raspberrypi", mac_addresses=[mac])
    _approve(client, org, headers, first["request_id"])
    device_id = _claim(client, first)["device_id"]
    renamed = client.patch(
        f"/api/v1/organizations/{org.id}/devices/{device_id}", headers=headers, json={"name": "Lobby"}
    )
    assert renamed.status_code == 200, renamed.text

    again = _submit(client, key, hostname="raspberrypi", mac_addresses=[mac])
    assert _suggestions(client, org, headers)[again["request_id"]] == "Lobby"
    _approve(client, org, headers, again["request_id"])
    assert _claim(client, again)["name"] == "Lobby"


def test_token_registration_uses_the_same_defaults(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    for mac, expected in (("aa:bb:cc:dd:00:08", "kiosk"), ("aa:bb:cc:dd:00:09", "kiosk-2")):
        response = client.post(
            "/api/v1/agent/register",
            json={"token": token, "hostname": "kiosk", "mac_addresses": [mac]},
        )
        assert response.status_code in (200, 201), response.text
        assert response.json()["name"] == expected


def test_renaming_a_device_keeps_its_public_address(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="Kiosk 12", mac="aa:bb:cc:dd:00:0a")
    base = f"/api/v1/organizations/{org.id}"
    address = f"shop.kiosk-12.{org.slug}.meteor-edge.com"
    created = client.post(
        f"{base}/endpoints", headers=headers, json={"device_id": device["device_id"], "hostname": address, "port": 80}
    )
    assert created.status_code == 201, created.text
    renamed = client.patch(f"{base}/devices/{device['device_id']}", headers=headers, json={"name": "Lobby"})
    assert renamed.status_code == 200, renamed.text
    assert client.get(f"{base}/endpoints/{created.json()['id']}", headers=headers).json()["hostname"] == address


def test_status_filter_still_counts_names_held_by_other_requests(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    approved = _submit(client, key, hostname="other", mac_addresses=["aa:bb:cc:dd:00:0b"])
    _approve(client, org, headers, approved["request_id"], name="kiosk")
    pending = _submit(client, key, hostname="kiosk", mac_addresses=["aa:bb:cc:dd:00:0c"])
    rows = client.get(
        f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers, params={"status": "pending"}
    ).json()
    assert [(row["id"], row["suggested_name"]) for row in rows] == [(pending["request_id"], "kiosk-2")]


def test_ambiguous_hardware_gets_no_existing_device_name(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    first = _submit(client, key, hostname="first", serial_number="S1", mac_addresses=["aa:bb:cc:dd:00:0d"])
    _approve(client, org, headers, first["request_id"])
    second = _submit(client, key, hostname="second", serial_number="S2", mac_addresses=["aa:bb:cc:dd:00:0e"])
    _approve(client, org, headers, second["request_id"])
    first_id = _claim(client, first)["device_id"]
    second_id = _claim(client, second)["device_id"]
    # Two devices now report the same serial, which a claim refuses as ambiguous.
    device = db_session.get(Device, UUID(second_id))
    device.serial_number = "S1"
    db_session.commit()
    assert first_id != second_id

    request = _submit(client, key, hostname="third", serial_number="S1", mac_addresses=["aa:bb:cc:dd:00:0f"])
    assert _suggestions(client, org, headers)[request["request_id"]] == "third"


def test_device_list_filters_by_exact_name(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    for index, name in enumerate(("Lobby", "Lobby 2", "Old lobby")):
        _register_device(client, token, name=name, mac=f"aa:bb:cc:dd:01:0{index}")
    listed = client.get(f"/api/v1/organizations/{org.id}/devices", headers=headers, params={"name": "lobby"}).json()
    assert [item["name"] for item in listed["items"]] == ["Lobby"]
