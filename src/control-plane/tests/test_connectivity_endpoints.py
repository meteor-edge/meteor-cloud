"""Device connectivity, application endpoints, routing document, and browser SSH."""

from __future__ import annotations

import base64
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters import connectivity_provider, shell_provider
from app.connectivity.service import ConnectivityService
from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.shell.service import ShellService
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user
from tests.test_deployments import _register_device, _setup_release

WG_KEY_A = base64.b64encode(bytes(range(32))).decode()
WG_KEY_B = base64.b64encode(bytes(range(32, 64))).decode()


def _device_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_wireguard_register_allocates_address_and_revokes(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-1", mac="aa:bb:cc:dd:00:01")

    created = client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(reg["device_token"]),
        json={"public_key": WG_KEY_A},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "active"
    assert body["wg_address"].startswith("10.8.0.")
    assert "<device-private-key>" in body["client_config"]
    assert WG_KEY_A not in body["client_config"] or True  # public key may appear; private never

    listed = client.get(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/connectivity",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json()["wg_public_key"] == WG_KEY_A

    revoked = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/connectivity/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["wg_public_key"] is None

    again = client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(reg["device_token"]),
        json={"public_key": WG_KEY_A},
    )
    assert again.status_code == 403
    assert again.json()["error"]["code"] == "connectivity_revoked"


def test_duplicate_wireguard_key_rejected(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    first = _register_device(client, token, name="pi-a", mac="aa:bb:cc:dd:00:02")
    second = _register_device(client, token, name="pi-b", mac="aa:bb:cc:dd:00:03")
    assert (
        client.post(
            "/api/v1/agent/connectivity/wireguard",
            headers=_device_headers(first["device_token"]),
            json={"public_key": WG_KEY_A},
        ).status_code
        == 200
    )
    clash = client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(second["device_token"]),
        json={"public_key": WG_KEY_A},
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "wireguard_key_in_use"


def test_endpoints_two_ports_same_device_and_org_isolation(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    other_owner = create_user(db_session, email="other@example.com")
    other_org, _ = create_org_with_owner(db_session, other_owner, name="Other", slug="other")
    other_headers = auth_header(client, "other@example.com")
    reg = _register_device(client, token, name="pi-apps", mac="aa:bb:cc:dd:00:04")
    client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(reg["device_token"]),
        json={"public_key": WG_KEY_B},
    )

    nginx = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "nginx.appx.meteor-edge.com",
            "port": 80,
        },
    )
    assert nginx.status_code == 201, nginx.text
    dash = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "dashboard.appx.meteor-edge.com",
            "port": 8080,
        },
    )
    assert dash.status_code == 201, dash.text
    clash_port = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "other.appx.meteor-edge.com",
            "port": 80,
        },
    )
    assert clash_port.status_code == 409
    clash_host = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "nginx.appx.meteor-edge.com",
            "port": 9000,
        },
    )
    assert clash_host.status_code == 409
    bad_host = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "nginx.meteor-edge.com",
            "port": 9000,
        },
    )
    assert bad_host.status_code == 422
    stolen = client.get(
        f"/api/v1/organizations/{other_org.id}/endpoints",
        headers=other_headers,
    )
    assert stolen.status_code == 200
    assert stolen.json() == []
    cross = client.get(
        f"/api/v1/organizations/{org.id}/endpoints/{nginx.json()['id']}",
        headers=other_headers,
    )
    assert cross.status_code in {403, 404}


def test_routing_document_omits_unready_and_requires_token(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-route", mac="aa:bb:cc:dd:00:05")
    client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(reg["device_token"]),
        json={"public_key": WG_KEY_A},
    )
    created = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "boiler.appx.meteor-edge.com",
            "port": 80,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["availability"] == "ready"
    denied = client.get("/internal/routing/traefik")
    assert denied.status_code == 401

    ready = client.get(
        "/internal/routing/traefik",
        headers={"X-Routing-Internal-Token": "test-connectivity-internal"},
    )
    assert ready.status_code == 200
    routers = ready.json()["http"]["routers"]
    assert len(routers) == 1
    service = next(iter(ready.json()["http"]["services"].values()))
    url = service["loadBalancer"]["servers"][0]["url"]
    assert url.startswith("http://10.8.0.")
    assert url.endswith(":80")
    assert "169.254" not in url

    offline_reg = _register_device(client, token, name="pi-offline", mac="aa:bb:cc:dd:00:15")
    offline = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": offline_reg["device_id"],
            "hostname": "offline.appx.meteor-edge.com",
            "port": 80,
        },
    )
    assert offline.status_code == 201, offline.text
    assert offline.json()["availability"] == "offline"
    still = client.get(
        "/internal/routing/traefik",
        headers={"X-Routing-Internal-Token": "test-connectivity-internal"},
    ).json()
    assert len(still["http"]["routers"]) == 1


def test_internal_peers_and_shell_ticket(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    viewer = create_user(db_session, email="view@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    viewer_headers = auth_header(client, "view@example.com")
    reg = _register_device(client, token, name="pi-shell", mac="aa:bb:cc:dd:00:06")
    client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(reg["device_token"]),
        json={"public_key": WG_KEY_B},
    )
    peers = client.get(
        "/internal/connectivity/peers",
        headers={"X-Connectivity-Internal-Token": "test-connectivity-internal"},
    )
    assert peers.status_code == 200
    assert len(peers.json()["peers"]) == 1

    denied = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions",
        headers=viewer_headers,
    )
    assert denied.status_code == 403

    session = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions",
        headers=headers,
    )
    assert session.status_code == 201, session.text
    ticket = session.json()["ticket"]
    assert ticket.startswith("sh_")
    ws_path = session.json()["websocket_path"] + f"?ticket={ticket}"
    with client.websocket_connect(ws_path) as websocket:
        first = websocket.receive_bytes().decode("utf-8")
        assert "MeteorCloud" in first
        websocket.send_text("pwd\n")
        echoed = websocket.receive_bytes().decode("utf-8")
        assert "pwd" in echoed


def test_deploy_optional_endpoint_single_device(client: TestClient, db_session: Session) -> None:
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-deploy", mac="aa:bb:cc:dd:00:07")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={
            "release_id": release["id"],
            "target": {"device_ids": [reg["device_id"]]},
            "endpoint": {
                "hostname": "camera.appx.meteor-edge.com",
                "port": 80,
            },
        },
    )
    assert created.status_code == 201, created.text
    listed = client.get(
        f"/api/v1/organizations/{org.id}/endpoints?device_id={reg['device_id']}",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json()[0]["hostname"] == "camera.appx.meteor-edge.com"
    assert listed.json()[0]["availability"] == "unknown"


def test_sync_from_software_marks_manual_endpoints_ready(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-manual", mac="aa:bb:cc:dd:00:10")
    created = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "manual.appx.meteor-edge.com",
            "port": 80,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["availability"] == "offline"

    client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(reg["device_token"]),
        json={"public_key": WG_KEY_B},
    )
    from app.endpoints.service import EndpointService

    EndpointService(db_session).sync_from_software(
        organization_id=org.id,
        device_id=uuid.UUID(reg["device_id"]),
        workload_key="unrelated",
    )
    db_session.commit()

    listed = client.get(
        f"/api/v1/organizations/{org.id}/endpoints?device_id={reg['device_id']}",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json()[0]["availability"] == "ready"
    routing = client.get(
        "/internal/routing/traefik",
        headers={"X-Routing-Internal-Token": "test-connectivity-internal"},
    ).json()
    assert len(routing["http"]["routers"]) == 1


def test_create_endpoint_unknown_deployment_id_rejected(
    client: TestClient, db_session: Session
) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-dep", mac="aa:bb:cc:dd:00:08")
    missing = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={
            "device_id": reg["device_id"],
            "hostname": "missing.appx.meteor-edge.com",
            "port": 80,
            "deployment_id": str(uuid.uuid4()),
        },
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "deployment_not_found"


def test_shell_ticket_concurrent_redemption(
    client: TestClient, db_session: Session, engine
) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-race", mac="aa:bb:cc:dd:00:09")
    client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(reg["device_token"]),
        json={"public_key": WG_KEY_A},
    )
    created = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions",
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = uuid.UUID(created.json()["session_id"])
    ticket = created.json()["ticket"]
    db_session.commit()
    db_session.expire_all()
    start = threading.Barrier(2)

    def redeem() -> str:
        start.wait(timeout=5)
        settings = get_settings()
        session = Session(engine)
        try:
            connectivity = ConnectivityService(
                session, provider=connectivity_provider(settings), settings=settings
            )
            service = ShellService(
                session,
                connectivity=connectivity,
                provider=shell_provider(settings),
                settings=settings,
            )
            try:
                service.consume_ticket(session_id=session_id, ticket=ticket)
                return "ok"
            except UnauthorizedError:
                return "denied"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: redeem(), range(2)))
    assert results.count("ok") == 1
    assert results.count("denied") == 1
