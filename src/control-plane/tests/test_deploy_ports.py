"""Device port conflicts and public-address reuse on Deploy."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.releases.kinds import HostPortBinding, docker_host_ports
from tests.test_deployments import _docker_web_release, _register_device, _setup_release


def test_docker_host_ports_parses_short_syntax() -> None:
    spec = {
        "ports": [
            "8080:80",
            "127.0.0.1:9000:9000",
            "[::1]:9001:9001",
            "53:53/udp",
            "80",
            "7000-7001:7000-7001",
            "bad:x",
        ]
    }
    assert docker_host_ports(spec) == [
        HostPortBinding(8080, None, "tcp"),
        HostPortBinding(9000, "127.0.0.1", "tcp"),
        HostPortBinding(9001, "::1", "tcp"),
        HostPortBinding(53, None, "udp"),
        HostPortBinding(7000, None, "tcp"),
        HostPortBinding(7001, None, "tcp"),
    ]
    assert docker_host_ports({"ports": ["0.0.0.0:8080:80"]})[0].host_ip is None
    assert docker_host_ports({"ports": ["127.0.0.1:8080:80"]})[0].loopback_only


def _docker_release(client: TestClient, org_id, headers, *, name: str, container: str, ports: list[str]) -> dict:
    created = client.post(
        f"/api/v1/organizations/{org_id}/releases",
        headers=headers,
        json={
            "name": name,
            "version": "1.0.0",
            "kind": "docker",
            "spec": {"container_name": container, "image": "nginx:alpine", "ports": ports},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _deploy(client: TestClient, org_id, headers, release_id: str, device_id: str, endpoint: dict | None = None):
    body: dict = {"release_id": release_id, "target": {"device_ids": [device_id]}}
    if endpoint is not None:
        body["endpoint"] = endpoint
    return client.post(f"/api/v1/organizations/{org_id}/deployments", headers=headers, json=body)


def test_deploy_rejects_host_port_used_by_another_workload(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-ports", mac="aa:bb:cc:dd:30:01")
    web = _docker_release(client, org.id, headers, name="web", container="web", ports=["8080:80"])
    other = _docker_release(client, org.id, headers, name="other", container="other", ports=["8080:3000"])

    assert _deploy(client, org.id, headers, web["id"], device["device_id"]).status_code == 201
    clash = _deploy(client, org.id, headers, other["id"], device["device_id"])
    assert clash.status_code == 409, clash.text
    assert clash.json()["error"]["code"] == "device_port_in_use"
    assert "8080/tcp on edge-ports is used by web" in clash.json()["error"]["message"]

    # Redeploying the same workload replaces it, so its own port is not a conflict.
    web_v2 = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "web",
            "version": "2.0.0",
            "kind": "docker",
            "spec": {"container_name": "web", "image": "nginx:alpine", "ports": ["8080:80"]},
        },
    ).json()
    assert _deploy(client, org.id, headers, web_v2["id"], device["device_id"]).status_code == 201


def test_endpoint_port_is_the_docker_host_port(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-host", mac="aa:bb:cc:dd:30:02")
    web = _docker_release(client, org.id, headers, name="web", container="web", ports=["8080:80"])

    wrong = _deploy(client, org.id, headers, web["id"], device["device_id"], {"port": 80})
    assert wrong.status_code == 422, wrong.text
    assert wrong.json()["error"]["code"] == "endpoint_port_mismatch"

    assert _deploy(client, org.id, headers, web["id"], device["device_id"], {"port": 8080}).status_code == 201


def test_endpoint_rejects_loopback_only_binding(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-lo", mac="aa:bb:cc:dd:30:03")
    web = _docker_release(client, org.id, headers, name="web", container="web", ports=["127.0.0.1:8080:80"])
    response = _deploy(client, org.id, headers, web["id"], device["device_id"], {"port": 8080})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "endpoint_port_loopback"


def test_endpoint_port_clashes_with_another_apps_public_address(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-url", mac="aa:bb:cc:dd:30:04")
    shop = _docker_web_release(client, org.id, headers, name="shop", ports=("9000:80",))
    assert _deploy(client, org.id, headers, shop["id"], device["device_id"], {"port": 9000}).status_code == 201

    web = _docker_release(client, org.id, headers, name="web", container="web", ports=["9000:80"])
    clash = _deploy(client, org.id, headers, web["id"], device["device_id"])
    assert clash.status_code == 409, clash.text
    assert "used by shop" in clash.json()["error"]["message"]


def test_redeploy_with_publish_keeps_the_existing_address(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    release = _docker_web_release(client, org.id, headers, ports=("8000:80", "8001:80"))
    device = _register_device(client, token, name="edge-again", mac="aa:bb:cc:dd:30:05")
    first = _deploy(client, org.id, headers, release["id"], device["device_id"], {"port": 8000})
    assert first.status_code == 201, first.text
    second = _deploy(client, org.id, headers, release["id"], device["device_id"], {"port": 8001})
    assert second.status_code == 201, second.text

    listed = client.get(
        f"/api/v1/organizations/{org.id}/endpoints", headers=headers, params={"device_id": device["device_id"]}
    ).json()
    assert len(listed) == 1
    assert listed[0]["port"] == 8001
    assert listed[0]["deployment_id"] == second.json()["id"]


def test_systemd_release_cannot_be_published(client: TestClient, db_session: Session) -> None:
    org, headers, token, systemd_release = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-unit", mac="aa:bb:cc:dd:30:06")
    response = _deploy(client, org.id, headers, systemd_release["id"], device["device_id"], {"port": 8080})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "endpoint_requires_container"
    listed = client.get(f"/api/v1/organizations/{org.id}/deployments", headers=headers).json()
    assert listed["total"] == 0  # refused before anything was recorded


def test_endpoint_needs_a_fixed_tcp_host_port(client: TestClient, db_session: Session) -> None:
    org, headers, token, _ = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-noport", mac="aa:bb:cc:dd:30:07")
    for index, ports in enumerate((["80"], [], ["8080:80/udp"])):
        release = _docker_release(client, org.id, headers, name=f"np{index}", container=f"np{index}", ports=ports)
        response = _deploy(client, org.id, headers, release["id"], device["device_id"], {"port": 8080})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "endpoint_port_mismatch"


def test_port_conflict_ignores_devices_that_skip_the_release(client: TestClient, db_session: Session) -> None:
    from app.devices.models import Device

    org, headers, token, _ = _setup_release(client, db_session)
    device = _register_device(client, token, name="edge-nodocker", mac="aa:bb:cc:dd:30:08")
    web = _docker_release(client, org.id, headers, name="web", container="web", ports=["8080:80"])
    assert _deploy(client, org.id, headers, web["id"], device["device_id"]).status_code == 201
    # The device then reports that docker is gone; a clashing release would be skipped there.
    row = db_session.get(Device, device["device_id"])
    row.metadata_ = {**(row.metadata_ or {}), "capabilities": {"docker": False, "systemd": True}}
    db_session.commit()

    other = _docker_release(client, org.id, headers, name="other", container="other", ports=["8080:3000"])
    response = _deploy(client, org.id, headers, other["id"], device["device_id"])
    assert response.status_code == 201, response.text
    assert response.json()["counts"]["skipped"] == 1


def test_release_rejects_a_host_port_range_too_wide_to_check(client: TestClient, db_session: Session) -> None:
    org, headers, _token, _ = _setup_release(client, db_session)
    response = client.post(
        f"/api/v1/organizations/{org.id}/releases",
        headers=headers,
        json={
            "name": "wide",
            "version": "1.0.0",
            "kind": "docker",
            "spec": {"container_name": "wide", "image": "nginx:alpine", "ports": ["10000-11024:10000-11024"]},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_release_spec"
    ok = _docker_release(client, org.id, headers, name="narrow", container="narrow", ports=["10000-11022:10000-11022"])
    assert len(docker_host_ports(ok["spec"])) == 1023
