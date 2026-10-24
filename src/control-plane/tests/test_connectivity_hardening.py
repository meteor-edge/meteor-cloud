"""Connectivity hardening: stable identity, recovery, revocation, host keys, shell tickets, routing."""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.ssh_gateway import SshGatewayProvider
from app.connectivity.models import DeviceConnectivity
from app.connectivity.schemas import AgentWireGuardRegisterRequest, ssh_host_key_fingerprint
from app.core.exceptions import ValidationAppError
from app.devices.models import Device
from app.shell.models import DeviceShellSession, ShellSessionStatus
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_connectivity_endpoints import INTERNAL, _device_headers, _report_handshake
from tests.test_deployments import _register_device, _setup_release

WG_KEY_A = base64.b64encode(bytes(range(32))).decode()
WG_KEY_B = base64.b64encode(bytes(range(32, 64))).decode()
WG_KEY_C = base64.b64encode(bytes(range(64, 96))).decode()
ROUTING = {"X-Routing-Internal-Token": "test-connectivity-internal"}


def _host_key(seed: int) -> str:
    """A well-formed ssh-ed25519 public key line (wire format: length-prefixed type + key)."""
    kind = b"ssh-ed25519"
    blob = len(kind).to_bytes(4, "big") + kind + (32).to_bytes(4, "big") + bytes([seed]) * 32
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} root@device"


HOST_KEY_1 = _host_key(1)
HOST_KEY_2 = _host_key(2)


def _register_wg(client: TestClient, device_token: str, public_key: str, **extra: str):
    return client.post(
        "/api/v1/agent/connectivity/wireguard",
        headers=_device_headers(device_token),
        json={"public_key": public_key, **extra},
    )


def _connectivity(client: TestClient, headers: dict[str, str], org_id, device_id: str) -> dict:
    response = client.get(f"/api/v1/organizations/{org_id}/devices/{device_id}/connectivity", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _peer_keys(client: TestClient) -> set[str]:
    response = client.get("/internal/connectivity/peers", headers=INTERNAL)
    assert response.status_code == 200
    return {peer["public_key"] for peer in response.json()["peers"]}


# --- Stable identity and idempotent registration -----------------------------


def test_reregistration_keeps_address_and_is_idempotent(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-stable", mac="aa:bb:cc:dd:01:01")
    first = _register_wg(client, reg["device_token"], WG_KEY_A)
    assert first.status_code == 200, first.text
    # Agent restarts and reconnects re-register the same key: same address, still one peer.
    for _ in range(3):
        again = _register_wg(client, reg["device_token"], WG_KEY_A)
        assert again.status_code == 200
        assert again.json()["wg_address"] == first.json()["wg_address"]
    assert _peer_keys(client) == {WG_KEY_A}
    assert db_session.query(DeviceConnectivity).count() == 1


def test_key_change_requires_reenroll(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-swap", mac="aa:bb:cc:dd:01:02")
    original = _register_wg(client, reg["device_token"], WG_KEY_A, ssh_host_key=HOST_KEY_1).json()

    swapped = _register_wg(client, reg["device_token"], WG_KEY_B)
    assert swapped.status_code == 409
    assert swapped.json()["error"]["code"] == "wireguard_key_changed"
    assert _peer_keys(client) == {WG_KEY_A}

    reenrolled = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/connectivity/reenroll", headers=headers
    )
    assert reenrolled.status_code == 200, reenrolled.text
    assert reenrolled.json()["state"] == "pending"
    assert reenrolled.json()["ssh_host_key_fingerprint"] is None
    assert _peer_keys(client) == set()

    recovered = _register_wg(client, reg["device_token"], WG_KEY_B, ssh_host_key=HOST_KEY_2)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "active"
    assert _peer_keys(client) == {WG_KEY_B}
    shown = _connectivity(client, headers, org.id, reg["device_id"])
    assert shown["ssh_host_key_fingerprint"] == ssh_host_key_fingerprint(HOST_KEY_2.rsplit(" ", 1)[0])
    assert original["wg_address"]


def test_reenroll_requires_device_update(client: TestClient, db_session: Session) -> None:
    org, _headers, token, _release = _setup_release(client, db_session)
    viewer = create_user(db_session, email="viewer-re@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    reg = _register_device(client, token, name="pi-re", mac="aa:bb:cc:dd:01:03")
    _register_wg(client, reg["device_token"], WG_KEY_A)
    denied = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/connectivity/reenroll",
        headers=auth_header(client, "viewer-re@example.com"),
    )
    assert denied.status_code == 403


def test_revoked_device_is_removed_and_stays_blocked_until_reenrolled(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-revoke", mac="aa:bb:cc:dd:01:04")
    _register_wg(client, reg["device_token"], WG_KEY_A)
    assert _peer_keys(client) == {WG_KEY_A}
    client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/connectivity/revoke", headers=headers)
    assert _peer_keys(client) == set()
    for key in (WG_KEY_A, WG_KEY_C):
        blocked = _register_wg(client, reg["device_token"], key)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "connectivity_revoked"
    assert _connectivity(client, headers, org.id, reg["device_id"])["state"] == "revoked"
    client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/connectivity/reenroll", headers=headers)
    assert _register_wg(client, reg["device_token"], WG_KEY_C).status_code == 200
    assert _peer_keys(client) == {WG_KEY_C}


def test_disabled_device_or_revoked_credential_drops_peer_and_routes(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-off", mac="aa:bb:cc:dd:01:05")
    _register_wg(client, reg["device_token"], WG_KEY_A)
    created = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={"device_id": reg["device_id"], "hostname": "pump.meteor-edge.com", "port": 80},
    )
    assert created.status_code == 201, created.text
    assert len(client.get("/internal/routing/traefik", headers=ROUTING).json()["http"]["routers"]) == 1

    client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/disable", headers=headers)
    assert _peer_keys(client) == set()
    assert client.get("/internal/routing/traefik", headers=ROUTING).json()["http"]["routers"] == {}

    # Re-enabling restores the same peer and route without any device action.
    client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/enable", headers=headers)
    assert _peer_keys(client) == {WG_KEY_A}
    assert len(client.get("/internal/routing/traefik", headers=ROUTING).json()["http"]["routers"]) == 1

    client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/revoke-credential", headers=headers)
    assert _peer_keys(client) == set()
    assert client.get("/internal/routing/traefik", headers=ROUTING).json()["http"]["routers"] == {}


def test_registration_retries_once_after_a_concurrent_insert(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.connectivity.service import ConnectivityService

    _org, _headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-race2", mac="aa:bb:cc:dd:01:06")
    real = ConnectivityService._register
    calls = {"n": 0}

    def flaky(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return real(self, **kwargs)

    monkeypatch.setattr(ConnectivityService, "_register", flaky)
    response = _register_wg(client, reg["device_token"], WG_KEY_A)
    assert response.status_code == 200, response.text
    assert calls["n"] == 2


# --- Connectivity state from evidence ------------------------------------------


def test_state_reflects_handshake_and_agent_checkin(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-state", mac="aa:bb:cc:dd:01:07")
    _register_wg(client, reg["device_token"], WG_KEY_A)
    device = db_session.get(Device, uuid.UUID(reg["device_id"]))

    device.last_seen_at = None
    db_session.commit()
    assert _connectivity(client, headers, org.id, reg["device_id"])["state"] == "offline"

    device.last_seen_at = datetime.now(UTC)
    db_session.commit()
    shown = _connectivity(client, headers, org.id, reg["device_id"])
    assert shown["state"] == "reconnecting"  # the agent checks in, the tunnel is not up
    assert shown["agent_last_seen_at"] is not None

    _report_handshake(client, WG_KEY_A)
    assert _connectivity(client, headers, org.id, reg["device_id"])["state"] == "connected"

    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    _report_handshake(client, WG_KEY_A, stale)
    assert _connectivity(client, headers, org.id, reg["device_id"])["state"] == "reconnecting"


# --- SSH host keys ---------------------------------------------------------------


def test_host_key_validation() -> None:
    body = AgentWireGuardRegisterRequest(public_key=WG_KEY_A, ssh_host_key=HOST_KEY_1)
    assert body.ssh_host_key == HOST_KEY_1.rsplit(" ", 1)[0]  # comment dropped
    assert AgentWireGuardRegisterRequest(public_key=WG_KEY_A, ssh_host_key="  ").ssh_host_key is None
    from pydantic import ValidationError

    for bad in ("ssh-dss AAAA", "ssh-ed25519 not*base64", f"ssh-rsa {HOST_KEY_1.split()[1]}"):
        with pytest.raises(ValidationError):
            AgentWireGuardRegisterRequest(public_key=WG_KEY_A, ssh_host_key=bad)


def test_host_key_pinned_on_first_report_and_mismatch_recorded(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="pi-hk", mac="aa:bb:cc:dd:01:08")
    _register_wg(client, reg["device_token"], WG_KEY_A, ssh_host_key=HOST_KEY_1)
    pinned = _connectivity(client, headers, org.id, reg["device_id"])["ssh_host_key_fingerprint"]
    assert pinned and pinned.startswith("SHA256:")

    changed = _register_wg(client, reg["device_token"], WG_KEY_A, ssh_host_key=HOST_KEY_2)
    assert changed.status_code == 200  # the tunnel keeps working
    shown = _connectivity(client, headers, org.id, reg["device_id"])
    assert shown["ssh_host_key_fingerprint"] == pinned  # never silently replaced
    assert "host key changed" in shown["last_ssh_error"]
    row = db_session.get(DeviceConnectivity, uuid.UUID(reg["device_id"]))
    db_session.refresh(row)
    assert row.ssh_host_key == HOST_KEY_1.rsplit(" ", 1)[0]


class _FakeProcess:
    def __init__(self, args, returncode=None, output=b""):
        self.args = list(args)
        self.returncode = returncode
        self.stdout = None
        self.stdin = None
        self._output = output

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_ssh_provider_pins_host_key() -> None:
    seen: list[_FakeProcess] = []

    def runner(args):
        process = _FakeProcess(args)
        seen.append(process)
        return process

    provider = SshGatewayProvider(identity_file="/run/key", runner=runner)
    key = HOST_KEY_1.rsplit(" ", 1)[0]
    connection = provider.connect(host="10.8.0.5", port=22, username="pi", host_key=key)
    args = seen[0].args
    assert "StrictHostKeyChecking=yes" in args
    assert "StrictHostKeyChecking=accept-new" not in args
    assert "GlobalKnownHostsFile=/dev/null" in args
    assert "HostKeyAlgorithms=ssh-ed25519" in args
    known = next(arg.split("=", 1)[1] for arg in args if arg.startswith("UserKnownHostsFile="))
    with open(known, encoding="utf-8") as handle:
        assert handle.read() == f"10.8.0.5 {key}\n"
    connection.close()
    assert not os.path.exists(known)

    provider.connect(host="10.8.0.5", port=2222, username="pi", host_key=key)
    known = next(arg.split("=", 1)[1] for arg in seen[1].args if arg.startswith("UserKnownHostsFile="))
    with open(known, encoding="utf-8") as handle:
        assert handle.read().startswith("[10.8.0.5]:2222 ")


def test_ssh_provider_unpinned_legacy_and_required_mode() -> None:
    seen: list[list[str]] = []
    provider = SshGatewayProvider(runner=lambda args: seen.append(list(args)) or _FakeProcess(args))
    provider.connect(host="10.8.0.5", port=22, username="pi")
    assert "StrictHostKeyChecking=accept-new" in seen[0]
    strict = SshGatewayProvider(require_host_key=True, runner=lambda args: _FakeProcess(args))
    with pytest.raises(ValidationAppError) as exc:
        strict.connect(host="10.8.0.5", port=22, username="pi")
    assert exc.value.code == "shell_host_key_unknown"
    with pytest.raises(ValidationAppError):
        strict.connect(host="192.168.1.10", port=22, username="pi", host_key="ssh-ed25519 AAAA")


def test_ssh_connection_reports_failure_line() -> None:
    from app.adapters.ssh_gateway import SshGatewayConnection

    process = _FakeProcess([], returncode=255)
    connection = SshGatewayConnection(process)  # type: ignore[arg-type]
    connection._remember(b"\x1b[0mHost key verification failed.\r\n")
    assert connection.failure == "Host key verification failed."
    process.returncode = 0
    assert connection.failure is None


# --- Browser shell tickets ---------------------------------------------------------


def _shell_ready_device(client: TestClient, db_session: Session, *, name: str, mac: str):
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name=name, mac=mac)
    _register_wg(client, reg["device_token"], WG_KEY_A)
    return org, headers, reg


def test_shell_refused_without_recent_handshake(client: TestClient, db_session: Session) -> None:
    org, headers, reg = _shell_ready_device(client, db_session, name="pi-stale", mac="aa:bb:cc:dd:01:09")
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    _report_handshake(client, WG_KEY_A, stale)
    response = client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_offline"


def test_shell_ticket_expired_reused_and_permission_removed(client: TestClient, db_session: Session) -> None:
    org, headers, reg = _shell_ready_device(client, db_session, name="pi-tix", mac="aa:bb:cc:dd:01:10")
    _report_handshake(client, WG_KEY_A)
    url = f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions"

    def ws_error(created: dict, ticket: str | None = None) -> str:
        path = created["websocket_path"] + f"?ticket={ticket or created['ticket']}"
        with client.websocket_connect(path) as websocket:
            return websocket.receive_text()

    # Expired.
    expired = client.post(url, headers=headers).json()
    row = db_session.get(DeviceShellSession, uuid.UUID(expired["session_id"]))
    row.ticket_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert "expired" in ws_error(expired)

    # Wrong ticket for a real session, then reuse of a spent one.
    used = client.post(url, headers=headers).json()
    assert "Invalid shell ticket" in ws_error(used, ticket="sh_wrong")
    with client.websocket_connect(used["websocket_path"] + f"?ticket={used['ticket']}") as websocket:
        assert "MeteorCloud" in websocket.receive_bytes().decode()
        websocket.send_text("exit\n")
        websocket.receive_bytes()
    assert "already used" in ws_error(used)

    # Access removed between issuing and redeeming the ticket.
    operator = create_user(db_session, email="op-shell@example.com")
    membership = add_member(db_session, org, operator, OrganizationRole.ADMIN)
    ticket = client.post(url, headers=auth_header(client, "op-shell@example.com"))
    assert ticket.status_code == 201, ticket.text
    db_session.delete(membership)
    db_session.commit()
    assert "no longer have shell access" in ws_error(ticket.json())
    spent = db_session.get(DeviceShellSession, uuid.UUID(ticket.json()["session_id"]))
    db_session.refresh(spent)
    assert spent.status == ShellSessionStatus.CLOSED.value


def test_shell_session_records_ssh_result(client: TestClient, db_session: Session) -> None:
    org, headers, reg = _shell_ready_device(client, db_session, name="pi-ok", mac="aa:bb:cc:dd:01:11")
    _report_handshake(client, WG_KEY_A)
    created = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions", headers=headers
    ).json()
    with client.websocket_connect(created["websocket_path"] + f"?ticket={created['ticket']}") as websocket:
        websocket.receive_bytes()
        websocket.send_text("exit\n")
        websocket.receive_bytes()
    shown = _connectivity(client, headers, org.id, reg["device_id"])
    assert shown["last_ssh_ok_at"] is not None
    assert shown["last_ssh_error"] is None


# --- Application routing -------------------------------------------------------------


def test_endpoint_cannot_publish_ssh_port(client: TestClient, db_session: Session) -> None:
    org, headers, reg = _shell_ready_device(client, db_session, name="pi-22", mac="aa:bb:cc:dd:01:12")
    created = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={"device_id": reg["device_id"], "hostname": "ssh.meteor-edge.com", "port": 22},
    )
    assert created.status_code == 422
    assert created.json()["error"]["code"] == "reserved_port"
    web = client.post(
        f"/api/v1/organizations/{org.id}/endpoints",
        headers=headers,
        json={"device_id": reg["device_id"], "hostname": "web.meteor-edge.com", "port": 80},
    ).json()
    moved = client.patch(f"/api/v1/organizations/{org.id}/endpoints/{web['id']}", headers=headers, json={"port": 22})
    assert moved.status_code == 422


def test_shell_target_cli_resolves_trusted_records(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import main as cli

    org, headers, reg = _shell_ready_device(client, db_session, name="pi-cli", mac="aa:bb:cc:dd:01:13")
    _register_wg(client, reg["device_token"], WG_KEY_A, ssh_host_key=HOST_KEY_1, ssh_user="pi")
    db_session.commit()
    monkeypatch.setattr(cli, "SessionLocal", lambda: Session(db_session.get_bind()))

    assert cli.shell_target(reg["device_id"]) == 3  # no handshake yet
    assert "tunnel is down" in capsys.readouterr().err

    _report_handshake(client, WG_KEY_A)
    assert cli.shell_target(reg["device_id"]) == 0
    user, host, port, key = capsys.readouterr().out.strip().split("\t")
    assert (user, port, key) == ("pi", "22", HOST_KEY_1.rsplit(" ", 1)[0])
    assert host.startswith("10.8.0.")
    assert cli.shell_target(host) == 0
    capsys.readouterr()

    client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/connectivity/revoke", headers=headers)
    assert cli.shell_target(reg["device_id"]) == 3
    assert "revoked" in capsys.readouterr().err
    assert cli.shell_target("not-a-device") == 2
