"""Device WiFi settings: org API never leaks PSK; agent gets PSK for own device only."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import auth_header, create_org_with_owner, create_user


def _register(client: TestClient, org_id, headers, **payload) -> dict:
    token = client.post(
        f"/api/v1/organizations/{org_id}/registration-tokens",
        headers=headers,
        json={"name": "Bootstrap", "max_uses": 100},
    ).json()["token"]
    body = {"token": token, **payload}
    return client.post("/api/v1/agent/register", json=body).json()


def test_wifi_put_get_hides_psk(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    reg = _register(client, org.id, headers, name="pi-1", mac_addresses=["aa:bb:cc:dd:ee:01"])

    put = client.put(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
        json={
            "ssid": "PlantFloor",
            "psk": "secretpassphrase",
            "interface": "wlan0",
            "hidden": False,
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["ssid"] == "PlantFloor"
    assert body["psk_set"] is True
    assert "psk" not in body
    assert body["revision"] == 1
    assert body["sync_status"] == "pending"

    got = client.get(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
    )
    assert got.status_code == 200
    assert "psk" not in got.json()
    assert got.json()["psk_set"] is True


def test_agent_network_includes_psk_only_for_own_device(
    client: TestClient, db_session: Session
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    a = _register(client, org.id, headers, name="pi-a", mac_addresses=["aa:bb:cc:dd:ee:0a"])
    b = _register(client, org.id, headers, name="pi-b", mac_addresses=["aa:bb:cc:dd:ee:0b"])

    client.put(
        f"/api/v1/organizations/{org.id}/devices/{a['device_id']}/wifi",
        headers=headers,
        json={"ssid": "Lab", "psk": "labpassword1"},
    )

    mine = client.get(
        "/api/v1/agent/network",
        headers={"Authorization": f"Bearer {a['device_token']}"},
    )
    assert mine.status_code == 200
    assert mine.json()["configured"] is True
    assert mine.json()["ssid"] == "Lab"
    assert mine.json()["psk"] == "labpassword1"

    other = client.get(
        "/api/v1/agent/network",
        headers={"Authorization": f"Bearer {b['device_token']}"},
    )
    assert other.status_code == 200
    assert other.json()["configured"] is False
    assert other.json().get("psk") is None


def test_wifi_rejects_short_psk_and_shell_metachar_iface(
    client: TestClient, db_session: Session
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    reg = _register(client, org.id, headers, name="pi-1", mac_addresses=["aa:bb:cc:dd:ee:01"])

    short = client.put(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
        json={"ssid": "Lab", "psk": "short"},
    )
    assert short.status_code == 422

    bad_iface = client.put(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
        json={"ssid": "Lab", "psk": "longenough", "interface": "wlan0;rm"},
    )
    assert bad_iface.status_code == 422


def test_wifi_report_ignores_stale_revision(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    reg = _register(client, org.id, headers, name="pi-1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    device_headers = {"Authorization": f"Bearer {reg['device_token']}"}

    client.put(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
        json={"ssid": "A", "psk": "passwordaaa"},
    )
    client.put(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
        json={"ssid": "B", "psk": "passwordbbb"},
    )
    # Desired is revision 2.
    client.post(
        "/api/v1/agent/heartbeat",
        headers=device_headers,
        json={
            "wifi_report": {
                "revision": 1,
                "applied_ssid": "A",
                "sync_status": "in_sync",
            }
        },
    )
    got = client.get(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
    ).json()
    assert got["revision"] == 2
    assert got["applied_ssid"] is None
    assert got["sync_status"] == "pending"

    client.post(
        "/api/v1/agent/heartbeat",
        headers=device_headers,
        json={
            "wifi_report": {
                "revision": 2,
                "applied_ssid": "B",
                "sync_status": "in_sync",
            }
        },
    )
    got = client.get(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
    ).json()
    assert got["applied_ssid"] == "B"
    assert got["sync_status"] == "in_sync"


def test_heartbeat_stores_network_links(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    reg = _register(client, org.id, headers, name="pi-1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        json={
            "network_links": [
                {
                    "name": "eth0",
                    "type": "ethernet",
                    "state": "connected",
                    "connection": "Wired connection 1",
                },
                {
                    "name": "wlan0",
                    "type": "wifi",
                    "state": "connected",
                    "connection": "meteorcloud-Lab",
                },
                {"name": "lo", "type": "loopback", "state": "connected"},
            ]
        },
    )
    device = client.get(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}",
        headers=headers,
    ).json()
    links = device["network_links"]
    assert len(links) == 2
    assert links[0]["name"] == "eth0"
    assert links[0]["type"] == "ethernet"
    assert links[1]["type"] == "wifi"


def test_wifi_clear(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, "owner@example.com")
    reg = _register(client, org.id, headers, name="pi-1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    client.put(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
        json={"ssid": "Lab", "psk": "labpassword1"},
    )
    deleted = client.delete(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
    )
    assert deleted.status_code == 204
    got = client.get(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/wifi",
        headers=headers,
    )
    assert got.status_code == 200
    assert got.json() is None
