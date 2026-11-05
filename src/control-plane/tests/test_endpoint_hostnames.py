"""Public app addresses: <app>.<device>.<org>.<domain> suggestions, custom names and availability."""

from __future__ import annotations

import base64
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.endpoints.models import ApplicationEndpoint
from app.endpoints.validation import (
    HostnameRejected,
    dns_label,
    suggested_hostname,
    tls_domain,
    validate_hostname,
)
from tests.conftest import auth_header, create_org_with_owner, create_user
from tests.test_deployments import _docker_web_release, _register_device, _setup_release

DOMAIN = "meteor-edge.com"
WG_KEY = base64.b64encode(bytes(range(64, 96))).decode()


def _reason(hostname: str, *, org: str = "acme", device: str = "Kiosk 12", short: bool = False) -> str:
    try:
        validate_hostname(
            hostname,
            apps_base_domain=DOMAIN,
            org_slug=org,
            device_name=device,
            reserved=frozenset({"my"}),
            allow_short_names=short,
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
        ("shop.acme.meteor-edge.com", "available"),
        ("www.acme.meteor-edge.com", "available"),
        ("shop.globex.meteor-edge.com", "not_yours"),
        ("shop.meteor-edge.com", "invalid"),
        ("shop.kiosk-12.globex.meteor-edge.com", "not_yours"),
        ("shop.other-device.acme.meteor-edge.com", "not_yours"),
        ("www.meteor-edge.com", "reserved"),
        ("my.meteor-edge.com", "reserved"),
        ("a.b.c.d.meteor-edge.com", "invalid"),
        ("shop.example.com", "invalid"),
        ("-shop.meteor-edge.com", "invalid"),
        ("meteor-edge.com", "invalid"),
        (f"{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 63}.meteor-edge.com", "invalid"),
    ],
)
def test_hostname_rules(hostname: str, reason: str) -> None:
    assert _reason(hostname) == reason


def test_short_names_need_the_setting() -> None:
    assert _reason("shop.meteor-edge.com", short=True) == "available"
    assert _reason("www.meteor-edge.com", short=True) == "reserved"
    assert _reason("my.meteor-edge.com", short=True) == "reserved"
    # Organization addresses work the same with the setting on.
    assert _reason("shop.acme.meteor-edge.com", short=True) == "available"
    assert _reason("shop.globex.meteor-edge.com", short=True) == "not_yours"


@pytest.mark.parametrize("org", ["api", "www", "my"])
def test_reserved_organization_slug_cannot_have_custom_names(org: str) -> None:
    assert _reason(f"shop.{org}.meteor-edge.com", org=org) == "reserved"
    # Its suggested addresses are unchanged.
    assert _reason(f"shop.kiosk-12.{org}.meteor-edge.com", org=org) == "available"


def test_rejections_are_validation_errors() -> None:
    with pytest.raises(ValidationAppError):
        validate_hostname("www.meteor-edge.com", apps_base_domain=DOMAIN, org_slug="acme", device_name="k")


def test_certificate_per_device_organization_or_domain() -> None:
    assert (
        tls_domain("shop.kiosk-12.acme.meteor-edge.com", apps_base_domain=DOMAIN) == "*.kiosk-12.acme.meteor-edge.com"
    )
    assert tls_domain("shop.acme.meteor-edge.com", apps_base_domain=DOMAIN) == "*.acme.meteor-edge.com"
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
    assert suggestion.json() == {
        "apps_domain": DOMAIN,
        "custom_domain": f"{org.slug}.{DOMAIN}",
        "hostname": expected,
        "available": True,
    }

    def check(hostname: str) -> dict:
        response = client.get(
            f"{base}/hostname-check", headers=headers, params={"device_id": device["device_id"], "hostname": hostname}
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert check(expected)["available"] is True
    assert check(f"shop.{org.slug}.{DOMAIN}")["available"] is True
    assert check(f"shop.{DOMAIN}")["reason"] == "invalid"
    assert check(f"shop.someone-else.{DOMAIN}")["reason"] == "not_yours"
    assert check(f"www.{DOMAIN}")["reason"] == "reserved"
    assert check(f"shop.kiosk-12.someone-else.{DOMAIN}")["reason"] == "not_yours"

    created = client.post(
        base, headers=headers, json={"device_id": device["device_id"], "hostname": expected, "port": 80}
    )
    assert created.status_code == 201, created.text
    custom = client.post(
        base,
        headers=headers,
        json={"device_id": device["device_id"], "hostname": f"shop.{org.slug}.{DOMAIN}", "port": 8080},
    )
    assert custom.status_code == 201, custom.text
    short = client.post(
        base, headers=headers, json={"device_id": device["device_id"], "hostname": f"shop.{DOMAIN}", "port": 8081}
    )
    assert short.status_code == 422

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


def _second_org_device(client: TestClient, db_session: Session) -> tuple[dict, dict, dict]:
    other_owner = create_user(db_session, email="globex@example.com")
    other_org, _ = create_org_with_owner(db_session, other_owner, name="Globex", slug="globex")
    other_headers = auth_header(client, "globex@example.com")
    other_token = client.post(
        f"/api/v1/organizations/{other_org.id}/registration-tokens", headers=other_headers, json={"name": "t"}
    ).json()["token"]
    return other_org, other_headers, _register_device(client, other_token, name="pi", mac="aa:bb:cc:dd:10:03")


def test_same_custom_name_in_two_organizations(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="pi", mac="aa:bb:cc:dd:10:02")
    assert (
        client.post(
            f"/api/v1/organizations/{org.id}/endpoints",
            headers=headers,
            json={"device_id": device["device_id"], "hostname": f"shop.{org.slug}.{DOMAIN}", "port": 80},
        ).status_code
        == 201
    )

    other_org, other_headers, other_device = _second_org_device(client, db_session)
    base = f"/api/v1/organizations/{other_org.id}/endpoints"
    # Another organization's address isn't reported as taken: it isn't theirs to ask about.
    foreign = client.get(
        f"{base}/hostname-check",
        headers=other_headers,
        params={"device_id": other_device["device_id"], "hostname": f"shop.{org.slug}.{DOMAIN}"},
    ).json()
    assert foreign["reason"] == "not_yours"
    own = client.get(
        f"{base}/hostname-check",
        headers=other_headers,
        params={"device_id": other_device["device_id"], "hostname": f"shop.globex.{DOMAIN}"},
    ).json()
    assert own["available"] is True
    suggestion = client.get(
        f"{base}/hostname-suggestion",
        headers=other_headers,
        params={"device_id": other_device["device_id"], "app": "shop"},
    ).json()
    assert suggestion["hostname"] == f"shop.pi.globex.{DOMAIN}"
    assert suggestion["custom_domain"] == f"globex.{DOMAIN}"
    assert suggestion["available"] is True


def test_short_names_when_allowed_are_shared_by_all_organizations(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "apps_allow_short_names", True)
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="pi", mac="aa:bb:cc:dd:10:02")
    base = f"/api/v1/organizations/{org.id}/endpoints"
    suggestion = client.get(
        f"{base}/hostname-suggestion", headers=headers, params={"device_id": device["device_id"], "app": "shop"}
    ).json()
    assert suggestion["custom_domain"] == DOMAIN
    created = client.post(
        base, headers=headers, json={"device_id": device["device_id"], "hostname": f"shop.{DOMAIN}", "port": 80}
    )
    assert created.status_code == 201, created.text

    other_org, other_headers, other_device = _second_org_device(client, db_session)
    result = client.get(
        f"/api/v1/organizations/{other_org.id}/endpoints/hostname-check",
        headers=other_headers,
        params={"device_id": other_device["device_id"], "hostname": f"shop.{DOMAIN}"},
    ).json()
    assert result["reason"] == "taken"


def test_existing_short_name_keeps_working(client: TestClient, db_session: Session) -> None:
    """Endpoints created before names had to include the organization aren't broken by edits or redeploys."""
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="pi", mac="aa:bb:cc:dd:10:06")
    base = f"/api/v1/organizations/{org.id}/endpoints"
    endpoint = client.post(
        base,
        headers=headers,
        json={"device_id": device["device_id"], "hostname": f"old.{org.slug}.{DOMAIN}", "port": 80},
    ).json()
    legacy = f"old.{DOMAIN}"
    row = db_session.get(ApplicationEndpoint, uuid.UUID(endpoint["id"]))
    row.hostname = legacy
    db_session.commit()

    own = client.get(
        f"{base}/hostname-check",
        headers=headers,
        params={"device_id": device["device_id"], "hostname": legacy, "endpoint_id": endpoint["id"]},
    ).json()
    assert own["available"] is True
    same = client.patch(f"{base}/{endpoint['id']}", headers=headers, json={"hostname": legacy, "port": 81})
    assert same.status_code == 200, same.text
    assert same.json()["hostname"] == legacy
    other_short = client.patch(f"{base}/{endpoint['id']}", headers=headers, json={"hostname": f"new.{DOMAIN}"})
    assert other_short.status_code == 422
    moved = client.patch(f"{base}/{endpoint['id']}", headers=headers, json={"hostname": f"new.{org.slug}.{DOMAIN}"})
    assert moved.status_code == 200, moved.text


def test_deploy_without_hostname_uses_suggestion(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    release = _docker_web_release(client, org.id, headers)
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


def test_routing_requests_one_certificate_per_device_or_organization(client: TestClient, db_session: Session) -> None:
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
        (f"friendly.{org.slug}.{DOMAIN}", 82),
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
        f"Host(`friendly.{org.slug}.{DOMAIN}`)": f"*.{org.slug}.{DOMAIN}",
    }


def test_address_longer_than_dns_allows_is_rejected() -> None:
    long_org = "o" * 63
    device = "d" * 63
    domain = f"{'x' * 63}.{DOMAIN}"
    hostname = f"{'a' * 63}.{device}.{long_org}.{domain}"
    with pytest.raises(HostnameRejected) as rejected:
        validate_hostname(hostname, apps_base_domain=domain, org_slug=long_org, device_name=device)
    assert rejected.value.reason == "invalid"
    assert "253" in rejected.value.message


def test_own_address_only_counts_on_its_own_device(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="pi", mac="aa:bb:cc:dd:10:07")
    other = _register_device(client, token, name="pi-2", mac="aa:bb:cc:dd:10:08")
    base = f"/api/v1/organizations/{org.id}/endpoints"
    hostname = f"own.{org.slug}.{DOMAIN}"
    endpoint = client.post(
        base, headers=headers, json={"device_id": device["device_id"], "hostname": hostname, "port": 80}
    ).json()

    def check(device_id: str) -> dict:
        return client.get(
            f"{base}/hostname-check",
            headers=headers,
            params={"device_id": device_id, "hostname": hostname, "endpoint_id": endpoint["id"]},
        ).json()

    assert check(device["device_id"])["available"] is True
    assert check(other["device_id"])["reason"] == "taken"


def test_redeploy_keeps_short_address_however_it_is_spelled(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    release = _docker_web_release(client, org.id, headers)
    device = _register_device(client, token, name="pi", mac="aa:bb:cc:dd:10:09")
    deployments = f"/api/v1/organizations/{org.id}/deployments"
    body = {"release_id": release["id"], "target": {"device_ids": [device["device_id"]]}}
    first = client.post(
        deployments, headers=headers, json={**body, "endpoint": {"hostname": f"old.{org.slug}.{DOMAIN}", "port": 80}}
    )
    assert first.status_code == 201, first.text
    row = db_session.scalar(select(ApplicationEndpoint))
    row.hostname = f"old.{DOMAIN}"
    db_session.commit()

    again = client.post(
        deployments, headers=headers, json={**body, "endpoint": {"hostname": f"Old.{DOMAIN}.", "port": 80}}
    )
    assert again.status_code == 201, again.text
    db_session.expire_all()
    assert db_session.scalar(select(ApplicationEndpoint)).hostname == f"old.{DOMAIN}"
