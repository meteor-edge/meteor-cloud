"""Public app addresses: <app>.<device>.<org>.<domain> suggestions, custom names and availability."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationAppError
from app.endpoints.validation import (
    HostnameRejected,
    dns_label,
    suggested_hostname,
    tls_domain,
    validate_hostname,
)
from tests.conftest import auth_header, create_org_with_owner, create_user
from tests.test_deployments import _register_device, _setup_release

DOMAIN = "meteor-edge.com"
WG_KEY = base64.b64encode(bytes(range(64, 96))).decode()


def _reason(hostname: str, *, org: str = "acme", device: str = "Kiosk 12") -> str:
    try:
        validate_hostname(
            hostname, apps_base_domain=DOMAIN, org_slug=org, device_name=device, reserved=frozenset({"my"})
        )
    except HostnameRejected as exc:
        return exc.reason
    return "available"


def test_dns_label_and_suggestion() -> None:
    assert dns_label("Kiosk 12", "device") == "kiosk-12"
    assert dns_label("  ---  ", "device") == "device"
    assert dns_label("x" * 80, "app") == "x" * 63
    assert (
        suggested_hostname(app="Web Shop", device_name="Kiosk 12", org_slug="acme", apps_base_domain=DOMAIN)
        == "web-shop.kiosk-12.acme.meteor-edge.com"
    )


@pytest.mark.parametrize(
    ("hostname", "reason"),
    [
        ("shop.kiosk-12.acme.meteor-edge.com", "available"),
        ("shop.meteor-edge.com", "available"),
        ("shop.kiosk-12.globex.meteor-edge.com", "not_yours"),
        ("shop.other-device.acme.meteor-edge.com", "not_yours"),
        ("www.meteor-edge.com", "reserved"),
        ("my.meteor-edge.com", "reserved"),
        ("a.b.meteor-edge.com", "invalid"),
        ("a.b.c.d.meteor-edge.com", "invalid"),
        ("shop.example.com", "invalid"),
        ("-shop.meteor-edge.com", "invalid"),
        ("meteor-edge.com", "invalid"),
    ],
)
def test_hostname_rules(hostname: str, reason: str) -> None:
    assert _reason(hostname) == reason


def test_rejections_are_validation_errors() -> None:
    with pytest.raises(ValidationAppError):
        validate_hostname("www.meteor-edge.com", apps_base_domain=DOMAIN, org_slug="acme", device_name="k")


def test_certificate_per_device_or_domain() -> None:
    assert (
        tls_domain("shop.kiosk-12.acme.meteor-edge.com", apps_base_domain=DOMAIN) == "*.kiosk-12.acme.meteor-edge.com"
    )
    assert tls_domain("shop.meteor-edge.com", apps_base_domain=DOMAIN) == "*.meteor-edge.com"


def test_suggestion_check_and_create(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="Kiosk 12", mac="aa:bb:cc:dd:10:01")
    base = f"/api/v1/organizations/{org.id}/endpoints"

    suggestion = client.get(
        f"{base}/hostname-suggestion", headers=headers, params={"device_id": device["device_id"], "app": "Web Shop"}
    )
    assert suggestion.status_code == 200, suggestion.text
    expected = f"web-shop.kiosk-12.{org.slug}.{DOMAIN}"
    assert suggestion.json() == {"apps_domain": DOMAIN, "hostname": expected, "available": True}

    def check(hostname: str) -> dict:
        response = client.get(
            f"{base}/hostname-check", headers=headers, params={"device_id": device["device_id"], "hostname": hostname}
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert check(expected)["available"] is True
    assert check(f"shop.{DOMAIN}")["available"] is True
    assert check(f"www.{DOMAIN}")["reason"] == "reserved"
    assert check(f"shop.kiosk-12.someone-else.{DOMAIN}")["reason"] == "not_yours"

    created = client.post(
        base, headers=headers, json={"device_id": device["device_id"], "hostname": expected, "port": 80}
    )
    assert created.status_code == 201, created.text
    custom = client.post(
        base, headers=headers, json={"device_id": device["device_id"], "hostname": f"shop.{DOMAIN}", "port": 8080}
    )
    assert custom.status_code == 201, custom.text

    taken = check(expected)
    assert taken == {
        "hostname": expected,
        "available": False,
        "reason": "taken",
        "message": "This address is already in use.",
    }
    # Renaming an endpoint to its own address is not a conflict.
    own = client.get(
        f"{base}/hostname-check",
        headers=headers,
        params={"device_id": device["device_id"], "hostname": expected, "endpoint_id": created.json()["id"]},
    )
    assert own.json()["available"] is True
    # The next suggestion for the same app on this device skips the taken address.
    again = client.get(
        f"{base}/hostname-suggestion", headers=headers, params={"device_id": device["device_id"], "app": "Web Shop"}
    ).json()
    assert again["hostname"] == f"web-shop-2.kiosk-12.{org.slug}.{DOMAIN}"

    foreign = client.post(
        base,
        headers=headers,
        json={"device_id": device["device_id"], "hostname": f"x.kiosk-12.globex.{DOMAIN}", "port": 9000},
    )
    assert foreign.status_code == 422


def test_custom_name_taken_across_organizations(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="pi", mac="aa:bb:cc:dd:10:02")
    assert (
        client.post(
            f"/api/v1/organizations/{org.id}/endpoints",
            headers=headers,
            json={"device_id": device["device_id"], "hostname": f"shop.{DOMAIN}", "port": 80},
        ).status_code
        == 201
    )

    other_owner = create_user(db_session, email="globex@example.com")
    other_org, _ = create_org_with_owner(db_session, other_owner, name="Globex", slug="globex")
    other_headers = auth_header(client, "globex@example.com")
    other_token = client.post(
        f"/api/v1/organizations/{other_org.id}/registration-tokens", headers=other_headers, json={"name": "t"}
    ).json()["token"]
    other_device = _register_device(client, other_token, name="pi", mac="aa:bb:cc:dd:10:03")
    result = client.get(
        f"/api/v1/organizations/{other_org.id}/endpoints/hostname-check",
        headers=other_headers,
        params={"device_id": other_device["device_id"], "hostname": f"shop.{DOMAIN}"},
    ).json()
    assert result["reason"] == "taken"
    # The suggested address never collides across organizations: the org is part of it.
    suggestion = client.get(
        f"/api/v1/organizations/{other_org.id}/endpoints/hostname-suggestion",
        headers=other_headers,
        params={"device_id": other_device["device_id"], "app": "shop"},
    ).json()
    assert suggestion["hostname"] == f"shop.pi.globex.{DOMAIN}"
    assert suggestion["available"] is True


def test_deploy_without_hostname_uses_suggestion(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    device = _register_device(client, token, name="Pi Deploy", mac="aa:bb:cc:dd:10:04")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [device["device_id"]]},
            "endpoint": {"port": 80},
        },
    )
    assert created.status_code == 201, created.text
    listed = client.get(
        f"/api/v1/organizations/{org.id}/endpoints", headers=headers, params={"device_id": device["device_id"]}
    ).json()
    assert [e["hostname"] for e in listed] == [f"camera-agent.pi-deploy.{org.slug}.{DOMAIN}"]


def test_routing_requests_one_certificate_per_device(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="Router Pi", mac="aa:bb:cc:dd:10:05")
    client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers={"Authorization": f"Bearer {device['device_token']}"},
        json={"public_key": WG_KEY},
    )
    base = f"/api/v1/organizations/{org.id}/endpoints"
    for hostname, port in (
        (f"a.router-pi.{org.slug}.{DOMAIN}", 80),
        (f"b.router-pi.{org.slug}.{DOMAIN}", 81),
        (f"friendly.{DOMAIN}", 82),
    ):
        response = client.post(
            base, headers=headers, json={"device_id": device["device_id"], "hostname": hostname, "port": port}
        )
        assert response.status_code == 201, response.text
    document = client.get(
        "/internal/routing/traefik", headers={"X-Routing-Internal-Token": "test-connectivity-internal"}
    ).json()
    certificates = {r["rule"]: r["tls"]["domains"][0]["main"] for r in document["http"]["routers"].values()}
    assert certificates == {
        f"Host(`a.router-pi.{org.slug}.{DOMAIN}`)": f"*.router-pi.{org.slug}.{DOMAIN}",
        f"Host(`b.router-pi.{org.slug}.{DOMAIN}`)": f"*.router-pi.{org.slug}.{DOMAIN}",
        f"Host(`friendly.{DOMAIN}`)": f"*.{DOMAIN}",
    }
