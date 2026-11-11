"""Wi-Fi changes from the console: who may make them, and what happens to the password."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.network.models import DeviceWifiNetwork
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_deployments import _register_device, _setup_release

PASSWORD = "correct-horse-battery"


def _device(client: TestClient, db_session: Session):
    org, headers, token, _release = _setup_release(client, db_session)
    registered = _register_device(client, token, name="kiosk", mac="aa:bb:cc:dd:10:01")
    agent = {"Authorization": f"Bearer {registered['device_token']}"}
    url = f"/api/v1/organizations/{org.id}/devices/{registered['device_id']}/wifi"
    return org, headers, agent, url


def _report(client: TestClient, agent: dict, **body) -> dict:
    response = client.post("/api/v1/agent/network/wifi/report", headers=agent, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _changes(client: TestClient, agent: dict) -> list[dict]:
    response = client.get("/api/v1/agent/network/wifi", headers=agent)
    assert response.status_code == 200, response.text
    return response.json()["changes"]


def _heartbeat(client: TestClient, agent: dict) -> dict:
    response = client.post("/api/v1/agent/heartbeat", headers=agent, json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_password_reaches_the_device_once_and_is_erased_when_it_reports(
    client: TestClient, db_session: Session, caplog
) -> None:
    org, headers, agent, url = _device(client, db_session)
    assert client.get(url, headers=headers).json() == {"status": None, "networks": []}
    assert _heartbeat(client, agent)["wifi_pending"] is False

    with caplog.at_level(logging.DEBUG):
        set_response = client.put(
            f"{url}/networks", headers=headers, json={"ssid": "Shop floor", "password": PASSWORD, "connect_now": True}
        )
    assert set_response.status_code == 200, set_response.text
    assert PASSWORD not in set_response.text
    assert PASSWORD not in caplog.text
    [network] = set_response.json()["networks"]
    assert (network["state"], network["action"], network["password_pending"]) == ("pending", "apply", True)

    # At rest it is encrypted, and the audit trail names the network, not the password.
    row = db_session.scalars(select(DeviceWifiNetwork)).one()
    assert row.password_sealed is not None and PASSWORD.encode() not in row.password_sealed
    events = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "device.wifi.set")).all()
    assert [event.metadata_["ssid"] for event in events] == ["Shop floor"]
    assert PASSWORD not in str([event.metadata_ for event in events])

    assert _heartbeat(client, agent)["wifi_pending"] is True
    [change] = _changes(client, agent)
    assert change == {
        "id": network["id"],
        "revision": 1,
        "action": "apply",
        "ssid": "Shop floor",
        "security": "wpa-psk",
        "hidden": False,
        "connect_now": True,
        "password": PASSWORD,
    }

    accepted = _report(
        client,
        agent,
        status={
            "supported": True,
            "connected_ssid": "Shop floor",
            "signal_percent": 71,
            "interface": "wlan0",
            "saved_networks": [{"ssid": "Shop floor", "managed": True}, {"ssid": "Factory", "managed": False}],
        },
        results=[{"id": network["id"], "revision": 1, "state": "applied"}],
    )
    assert accepted == {"accepted": 1}
    db_session.expire_all()
    assert db_session.scalars(select(DeviceWifiNetwork)).one().password_sealed is None
    assert _changes(client, agent) == []
    assert _heartbeat(client, agent)["wifi_pending"] is False

    view = client.get(url, headers=headers).json()
    assert view["status"]["connected_ssid"] == "Shop floor"
    assert view["status"]["saved_networks"][1] == {"ssid": "Factory", "managed": False}
    assert (view["networks"][0]["state"], view["networks"][0]["password_pending"]) == ("applied", False)
    assert PASSWORD not in str(view)


def test_rollback_and_failure_are_shown_and_also_erase_the_password(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    client.put(f"{url}/networks", headers=headers, json={"ssid": "New", "password": PASSWORD, "connect_now": True})
    [change] = _changes(client, agent)
    _report(
        client,
        agent,
        results=[
            {
                "id": change["id"],
                "revision": change["revision"],
                "state": "rolled_back",
                "error": "MeteorCloud was not reachable through New within 90 seconds",
            }
        ],
    )
    [network] = client.get(url, headers=headers).json()["networks"]
    assert network["state"] == "rolled_back"
    assert "not reachable" in network["last_error"]
    assert network["password_pending"] is False

    # Trying again is a new revision; a late report about the old one changes nothing.
    client.put(f"{url}/networks", headers=headers, json={"ssid": "New", "password": PASSWORD})
    assert _report(client, agent, results=[{"id": change["id"], "revision": 1, "state": "applied"}]) == {"accepted": 0}
    [retry] = _changes(client, agent)
    assert (retry["revision"], retry["connect_now"]) == (2, False)


def test_only_owners_and_admins_change_wifi_and_devices_see_only_their_own(
    client: TestClient, db_session: Session
) -> None:
    org, headers, agent, url = _device(client, db_session)
    for email, role in (("operator@example.com", OrganizationRole.OPERATOR), ("viewer@example.com", OrganizationRole.VIEWER)):
        add_member(db_session, org, create_user(db_session, email=email), role)
        member = auth_header(client, email)
        assert client.get(url, headers=member).status_code == 200
        refused = client.put(f"{url}/networks", headers=member, json={"ssid": "Evil", "password": PASSWORD})
        assert refused.status_code == 403, refused.text
        assert client.post(f"{url}/networks/remove", headers=member, json={"ssid": "Evil"}).status_code == 403
    create_user(db_session, email="outsider@example.com")
    assert client.get(url, headers=auth_header(client, "outsider@example.com")).status_code == 404

    client.put(f"{url}/networks", headers=headers, json={"ssid": "Mine", "password": PASSWORD})
    assert client.get("/api/v1/agent/network/wifi").status_code == 401
    # Another device gets nothing, and cannot settle this device's change.
    token = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": "Second"}
    ).json()["token"]
    other = _register_device(client, token, name="other", mac="aa:bb:cc:dd:10:02")
    other_agent = {"Authorization": f"Bearer {other['device_token']}"}
    assert _changes(client, other_agent) == []
    [change] = _changes(client, agent)
    assert _report(client, other_agent, results=[{"id": change["id"], "revision": 1, "state": "applied"}]) == {
        "accepted": 0
    }


def test_validation_and_open_networks(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    for body in (
        {"ssid": "Net", "password": "short"},
        {"ssid": "Net"},
        {"ssid": "", "password": PASSWORD},
        {"ssid": "x" * 33, "password": PASSWORD},
        {"ssid": "Net", "password": "line\nbreak-password"},
        {"ssid": "Net", "security": "open", "password": PASSWORD},
        {"ssid": "Net", "security": "wep", "password": PASSWORD},
    ):
        response = client.put(f"{url}/networks", headers=headers, json=body)
        assert response.status_code == 422, body
        assert "break-password" not in response.text
    assert client.put(f"{url}/networks", headers=headers, json={"ssid": "Guest", "security": "open"}).status_code == 200
    [change] = _changes(client, agent)
    assert (change["security"], change["password"]) == ("open", None)


def test_removing_a_network_is_refused_when_it_would_cut_the_device_off(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    saved = [{"ssid": "Old", "managed": False}, {"ssid": "Spare", "managed": False}]
    _report(client, agent, status={"supported": True, "connected_ssid": "Old", "saved_networks": saved})

    refused = client.post(f"{url}/networks/remove", headers=headers, json={"ssid": "Old"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "wifi_network_in_use"
    assert client.post(f"{url}/networks/remove", headers=headers, json={"ssid": "Nowhere"}).status_code == 404

    # A network the device is not using (even one written at image time) can go.
    removed = client.post(f"{url}/networks/remove", headers=headers, json={"ssid": "Spare"})
    assert removed.status_code == 200, removed.text
    [change] = _changes(client, agent)
    assert (change["action"], change["ssid"], change["password"]) == ("remove", "Spare", None)
    _report(client, agent, results=[{"id": change["id"], "revision": change["revision"], "state": "removed"}])
    assert client.get(url, headers=headers).json()["networks"] == []

    # With a cable in, the connected network may be removed too.
    _report(
        client, agent, status={"supported": True, "connected_ssid": "Old", "wired_connected": True, "saved_networks": saved}
    )
    assert client.post(f"{url}/networks/remove", headers=headers, json={"ssid": "Old"}).status_code == 200

    # A change the device never collected is simply withdrawn.
    client.put(f"{url}/networks", headers=headers, json={"ssid": "Typo", "password": PASSWORD})
    withdrawn = client.post(f"{url}/networks/remove", headers=headers, json={"ssid": "Typo"})
    assert [item["ssid"] for item in withdrawn.json()["networks"]] == ["Old"]


def test_uncollected_password_is_erased_after_its_time(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    client.put(f"{url}/networks", headers=headers, json={"ssid": "Late", "password": PASSWORD})
    row = db_session.scalars(select(DeviceWifiNetwork)).one()
    row.password_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert _changes(client, agent) == []
    db_session.expire_all()
    row = db_session.scalars(select(DeviceWifiNetwork)).one()
    assert (row.state, row.password_sealed) == ("failed", None)
    [network] = client.get(url, headers=headers).json()["networks"]
    assert "Enter it again" in network["last_error"]


def test_password_sealed_for_one_change_does_not_open_as_another(client: TestClient, db_session: Session) -> None:
    _org, headers, agent, url = _device(client, db_session)
    client.put(f"{url}/networks", headers=headers, json={"ssid": "A", "password": PASSWORD})
    client.put(f"{url}/networks", headers=headers, json={"ssid": "B", "password": "another-password"})
    first, second = db_session.scalars(select(DeviceWifiNetwork).order_by(DeviceWifiNetwork.ssid)).all()
    second.password_sealed = first.password_sealed
    db_session.commit()

    changes = _changes(client, agent)
    assert [(item["ssid"], item["password"]) for item in changes] == [("A", PASSWORD)]
    networks = {item["ssid"]: item for item in client.get(url, headers=headers).json()["networks"]}
    assert networks["B"]["state"] == "failed"
