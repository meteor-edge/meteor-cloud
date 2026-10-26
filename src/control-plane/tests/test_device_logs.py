"""Device Logs window: sources listed on demand, device.logs, log sessions over the SSH bridge."""

from __future__ import annotations

import json
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

from app.adapters.ssh_gateway import EchoShellConnection, EchoShellProvider, SshGatewayProvider
from app.audit.models import AuditEvent
from app.authorization.catalog import SYSTEM_ROLES
from app.ports.shell import ShellCertificate
from app.shell.logs import LIST_COMMAND, LOGS_HELPER, LogSessionCreateRequest, log_command, parse_log_sources
from app.shell.models import DeviceShellSession, SessionKind
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_connectivity_endpoints import _report_handshake
from tests.test_connectivity_hardening import WG_KEY_A, _FakeProcess, _register_wg
from tests.test_deployments import _register_device, _setup_release

SOURCES = [
    {"kind": "journal", "name": ""},
    {"kind": "systemd", "name": "nginx.service", "state": "active"},
    {"kind": "docker", "name": "web", "state": "running", "project": "shop"},
    {"kind": "file", "name": "/var/log/nginx/access.log", "size": 42},
]


def _logs_device(client: TestClient, db_session: Session, *, name: str, mac: str):
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name=name, mac=mac)
    _register_wg(client, reg["device_token"], WG_KEY_A)
    _report_handshake(client, WG_KEY_A)
    return org, headers, reg


def _listed(body: dict) -> list[dict]:
    return [{key: value for key, value in item.items() if value is not None} for item in body["sources"]]


@pytest.fixture
def device_lists(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The echo provider answers ``meteorcli-logs list`` with SOURCES; records each command."""
    commands: list[list[str]] = []
    connect = EchoShellProvider.connect

    def fake_connect(self, **kwargs):
        commands.append(list(kwargs.get("command") or []))
        if kwargs.get("command") == LIST_COMMAND:
            return EchoShellConnection(banner=json.dumps([*SOURCES, {"kind": "shell", "name": "x"}]), closed=True)
        return connect(self, **kwargs)

    monkeypatch.setattr(EchoShellProvider, "connect", fake_connect)
    return commands


# --- Pure validation ------------------------------------------------------------------


def test_listed_sources_are_parsed() -> None:
    raw = json.dumps([*SOURCES, {"kind": "shell", "name": "bash"}, "junk"]).encode()
    sources = parse_log_sources(raw)
    assert sources is not None
    assert [source.model_dump(exclude_none=True) for source in sources] == SOURCES
    assert parse_log_sources(b"sudo: a password is required") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "systemd", "name": "-x.service"},
        {"kind": "docker", "name": ""},
        {"kind": "docker", "name": "a\nb"},
        {"kind": "journal", "name": "x"},
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


def test_api_lists_sources_from_the_device(
    client: TestClient, db_session: Session, device_lists: list[list[str]]
) -> None:
    org, headers, reg = _logs_device(client, db_session, name="pi-logs", mac="aa:bb:cc:dd:03:01")
    url = f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/log-sources"
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    assert _listed(response.json()) == SOURCES
    assert device_lists == [LIST_COMMAND]


def test_device_without_helper_is_a_clear_error(client: TestClient, db_session: Session) -> None:
    # The plain echo provider prints the command back, which is not a source list.
    org, headers, reg = _logs_device(client, db_session, name="pi-nohelper", mac="aa:bb:cc:dd:03:05")
    response = client.get(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/log-sources", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "log_sources_unavailable"


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


def test_device_logs_permission(client: TestClient, db_session: Session, device_lists: list[list[str]]) -> None:
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
