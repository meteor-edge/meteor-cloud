"""Device Logs window: reported sources, device.logs, log sessions over the SSH bridge."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.ssh_gateway import SshGatewayProvider
from app.audit.models import AuditEvent
from app.authorization.catalog import SYSTEM_ROLES
from app.ports.shell import ShellCertificate
from app.shell.logs import LOGS_HELPER, LogSessionCreateRequest, clean_log_sources, log_command
from app.shell.models import DeviceShellSession, SessionKind
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_connectivity_endpoints import _device_headers, _report_handshake
from tests.test_connectivity_hardening import WG_KEY_A, _FakeProcess, _register_wg
from tests.test_deployments import _register_device, _setup_release

SOURCES = [
    {"kind": "journal", "name": ""},
    {"kind": "systemd", "name": "nginx.service", "state": "active"},
    {"kind": "docker", "name": "web", "state": "running", "project": "shop"},
    {"kind": "file", "name": "/var/log/nginx/access.log", "size": 42},
]


def _logs_device(client: TestClient, db_session: Session, *, name: str, mac: str, sources=SOURCES):
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name=name, mac=mac)
    _register_wg(client, reg["device_token"], WG_KEY_A)
    _report_handshake(client, WG_KEY_A)
    if sources is not None:
        response = client.post(
            "/api/v1/agent/heartbeat",
            headers=_device_headers(reg["device_token"]),
            json={"log_sources": sources},
        )
        assert response.status_code == 200, response.text
    return org, headers, reg


def _reported(body: dict) -> list[dict]:
    return [{key: value for key, value in item.items() if value is not None} for item in body["sources"]]


# --- Pure validation ------------------------------------------------------------------


def test_reported_sources_are_cleaned() -> None:
    raw = [
        *SOURCES,
        {"kind": "systemd", "name": "nginx.service;reboot"},
        {"kind": "docker", "name": "--privileged"},
        {"kind": "file", "name": "/etc/shadow"},
        {"kind": "file", "name": "/var/log/../../etc/shadow"},
        {"kind": "journal", "name": "x"},
        {"kind": "shell", "name": "bash"},
        "junk",
    ]
    assert clean_log_sources(raw) == SOURCES


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "systemd", "name": "-x.service"},
        {"kind": "docker", "name": "web", "lines": 0},
        {"kind": "docker", "name": "web", "lines": 100000},
        {"kind": "docker", "name": "web", "since": "yesterday"},
        {"kind": "docker", "name": "web", "grep": "a\nb"},
        {"kind": "docker", "name": "web", "grep": "x" * 201},
    ],
)
def test_bad_log_requests_are_refused(payload: dict) -> None:
    with pytest.raises(ValidationError):
        LogSessionCreateRequest.model_validate(payload)


def test_log_command_is_fixed_argv() -> None:
    params = {
        "kind": "systemd",
        "name": "nginx.service",
        "lines": 50,
        "since": "1h",
        "grep": "it's; rm -rf /",
        "follow": True,
    }
    command = log_command(params)
    assert command == [
        "sudo",
        "-n",
        LOGS_HELPER,
        "show",
        "systemd",
        "nginx.service",
        "--lines",
        "50",
        "--since",
        "1h",
        "--grep",
        "it's; rm -rf /",
        "--follow",
    ]
    assert shlex.split(shlex.join(command)) == command
    assert log_command({"kind": "journal", "follow": False})[3:6] == ["show", "journal", "--lines"]


def test_system_roles_get_device_logs_but_not_viewer() -> None:
    roles = {role.key: role.permissions for role in SYSTEM_ROLES}
    for key in ("owner", "admin", "operator", "developer"):
        assert "device.logs" in roles[key], key
    assert "device.logs" not in roles["viewer"]
    assert "device.shell" not in roles["operator"]


# --- SSH adapter ----------------------------------------------------------------------


def test_ssh_provider_runs_quoted_command_without_pty() -> None:
    seen: list[list[str]] = []
    provider = SshGatewayProvider(runner=lambda args: seen.append(list(args)) or _FakeProcess(args))
    command = ["sudo", "-n", LOGS_HELPER, "show", "docker", "web", "--grep", "a b'c"]
    provider.connect(host="10.8.0.5", port=22, username="pi", command=command)
    args = seen[0]
    assert "-T" in args and "-tt" not in args
    assert args[-2] == "pi@10.8.0.5"
    assert shlex.split(args[-1]) == command
    provider.connect(host="10.8.0.5", port=22, username="pi")
    assert "-tt" in seen[1] and seen[1][-1] == "pi@10.8.0.5"


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_log_certificate_forces_the_command_and_has_no_pty(tmp_path: Path) -> None:
    ca = tmp_path / "ca"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test-ca", "-f", str(ca)], check=True)
    details: list[str] = []

    def runner(args):
        args = list(args)
        cert = args[args.index("-o", args.index("-i")) + 1].split("=", 1)[1]
        details.append(
            subprocess.run(["ssh-keygen", "-L", "-f", cert], check=True, capture_output=True, text=True).stdout
        )
        return subprocess.Popen(["true"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    forced = shlex.join(["sudo", "-n", LOGS_HELPER, "show", "journal", "--lines", "10"])
    provider = SshGatewayProvider(ca_file=str(ca), runner=runner)
    provider.connect(
        host="10.8.0.5",
        port=22,
        username="pi",
        certificate=ShellCertificate(principal="meteorcloud-device-abc", key_id="logs:s1:a@b", force_command=forced),
        command=shlex.split(forced),
    ).close()
    assert "force-command" in details[0]
    assert LOGS_HELPER in details[0]
    assert "permit-pty" not in details[0]


# --- API ------------------------------------------------------------------------------


def test_heartbeat_stores_sources_and_api_lists_them(client: TestClient, db_session: Session) -> None:
    org, headers, reg = _logs_device(client, db_session, name="pi-logs", mac="aa:bb:cc:dd:03:01")
    url = f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/log-sources"
    body = client.get(url, headers=headers).json()
    assert _reported(body) == SOURCES
    assert body["reported_at"] is not None
    # A heartbeat without log_sources (older agent) leaves the last report alone.
    client.post("/api/v1/agent/heartbeat", headers=_device_headers(reg["device_token"]), json={})
    assert _reported(client.get(url, headers=headers).json()) == SOURCES


def test_log_session_streams_the_helper_command(client: TestClient, db_session: Session) -> None:
    org, headers, reg = _logs_device(client, db_session, name="pi-stream", mac="aa:bb:cc:dd:03:02")
    created = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/log-sessions",
        headers=headers,
        json={"kind": "systemd", "name": "nginx.service", "lines": 100, "grep": "error", "follow": False},
    )
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["websocket_path"] == f"/api/v1/log-sessions/{session['session_id']}/ws"
    # The shell websocket never redeems a log ticket.
    shell_path = f"/api/v1/shell-sessions/{session['session_id']}/ws?ticket={session['ticket']}"
    with client.websocket_connect(shell_path) as websocket:
        assert "not found" in websocket.receive_text()

    with client.websocket_connect(session["websocket_path"]) as websocket:
        websocket.send_json({"type": "auth", "ticket": session["ticket"]})
        output = websocket.receive_bytes().decode()
        assert output.startswith("$ sudo -n " + LOGS_HELPER + " show systemd nginx.service --lines 100 --grep error")
        assert "end of log" in websocket.receive_text()

    row = db_session.get(DeviceShellSession, uuid.UUID(session["session_id"]))
    db_session.refresh(row)
    assert row.kind == SessionKind.LOGS.value
    assert row.status == "closed"
    actions = set(db_session.scalars(select(AuditEvent.action).where(AuditEvent.resource_id == str(row.device_id))))
    assert {"device.logs.start", "device.logs.end"} <= actions


def test_log_session_refuses_unreported_sources(client: TestClient, db_session: Session) -> None:
    org, headers, reg = _logs_device(client, db_session, name="pi-unrep", mac="aa:bb:cc:dd:03:03")
    url = f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/log-sessions"
    response = client.post(url, headers=headers, json={"kind": "docker", "name": "other"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "log_source_not_found"
    response = client.post(url, headers=headers, json={"kind": "file", "name": "/etc/shadow"})
    assert response.status_code == 422


def test_device_logs_permission(client: TestClient, db_session: Session) -> None:
    org, _headers, reg = _logs_device(client, db_session, name="pi-perm", mac="aa:bb:cc:dd:03:04")
    base = f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}"
    add_member(db_session, org, create_user(db_session, email="viewer-logs@example.com"), OrganizationRole.VIEWER)
    add_member(db_session, org, create_user(db_session, email="op-logs@example.com"), OrganizationRole.OPERATOR)
    viewer = auth_header(client, "viewer-logs@example.com")
    operator = auth_header(client, "op-logs@example.com")

    assert client.get(f"{base}/log-sources", headers=viewer).status_code == 403
    assert client.post(f"{base}/log-sessions", headers=viewer, json={"kind": "journal"}).status_code == 403
    assert client.get(f"{base}/log-sources", headers=operator).status_code == 200
    assert client.post(f"{base}/log-sessions", headers=operator, json={"kind": "journal"}).status_code == 201
    # Logs access does not open the terminal.
    assert client.post(f"{base}/shell-sessions", headers=operator).status_code == 403
