"""Short-lived SSH user certificates instead of the shared gateway key."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.ssh_gateway import EchoShellProvider, SshGatewayProvider
from app.audit.models import AuditEvent
from app.connectivity.service import ssh_principal
from app.core.config import Settings, get_settings
from app.ports.shell import ShellCertificate
from tests.test_connectivity_endpoints import _report_handshake
from tests.test_connectivity_hardening import WG_KEY_A, _connectivity, _register_wg
from tests.test_deployments import _register_device, _setup_release

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")


@pytest.fixture
def ca(tmp_path: Path) -> tuple[str, str]:
    """(private key path, public key "<type> <base64>") of a throw-away user CA."""
    key = tmp_path / "ca"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test-ca", "-f", str(key)], check=True)
    public = " ".join(key.with_name("ca.pub").read_text().split()[:2])
    return str(key), public


@pytest.fixture
def ca_settings(client: TestClient, ca: tuple[str, str]) -> Settings:
    settings = get_settings().model_copy(update={"shell_ssh_ca_file": ca[0], "shell_ssh_ca_public_key": ca[1]})
    client.app.dependency_overrides[get_settings] = lambda: settings
    return settings


def _certificate_details(cert: Path) -> str:
    return subprocess.run(["ssh-keygen", "-L", "-f", str(cert)], check=True, capture_output=True, text=True).stdout


def _device(client: TestClient, db_session: Session, *, name: str, mac: str):
    org, headers, token, _release = _setup_release(client, db_session)
    reg = _register_device(client, token, name=name, mac=mac)
    return org, headers, reg


# --- Adapter ------------------------------------------------------------------------


def test_provider_logs_in_with_a_fresh_certificate(ca: tuple[str, str]) -> None:
    seen: dict[str, object] = {}

    def runner(args):
        args = list(args)
        cert = Path(args[args.index("-o", args.index("-i")) + 1].split("=", 1)[1])
        seen.update(args=args, cert=cert, details=_certificate_details(cert), key=Path(args[args.index("-i") + 1]))
        return subprocess.Popen(["true"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    provider = SshGatewayProvider(identity_file="/run/gateway/id_ed25519", ca_file=ca[0], runner=runner)
    connection = provider.connect(
        host="10.8.0.5",
        port=22,
        username="pi",
        certificate=ShellCertificate(principal="meteorcloud-device-abc", key_id="shell:s1:ops@example.com"),
    )
    args = seen["args"]
    assert "/run/gateway/id_ed25519" not in args  # never the shared key alongside the certificate
    details = seen["details"]
    assert "meteorcloud-device-abc" in details
    assert "shell:s1:ops@example.com" in details
    assert "permit-pty" in details
    for forbidden in ("permit-agent-forwarding", "permit-port-forwarding", "permit-X11-forwarding"):
        assert forbidden not in details
    key_dir = seen["key"].parent
    assert oct(os.stat(key_dir).st_mode & 0o777) == "0o700"
    connection.close()
    assert not key_dir.exists()


def test_provider_keeps_gateway_key_without_certificate() -> None:
    seen: list[str] = []

    def runner(args):
        seen.extend(args)
        return subprocess.Popen(["true"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    SshGatewayProvider(identity_file="/run/gateway/id_ed25519", ca_file="/run/ca", runner=runner).connect(
        host="10.8.0.5", port=22, username="pi"
    )
    assert seen[seen.index("-i") + 1] == "/run/gateway/id_ed25519"
    assert not any(arg.startswith("CertificateFile=") for arg in seen)


# --- Registration -------------------------------------------------------------------


def test_registration_offers_the_ca_and_records_trust(
    client: TestClient, db_session: Session, ca_settings: Settings, tmp_path: Path
) -> None:
    org, headers, reg = _device(client, db_session, name="pi-ca", mac="aa:bb:cc:dd:02:01")
    first = _register_wg(client, reg["device_token"], WG_KEY_A)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["ssh_ca_public_key"] == ca_settings.shell_ssh_ca_public_key
    assert body["ssh_principal"] == f"meteorcloud-device-{reg['device_id']}"
    assert body["ssh_gateway_key_retired"] is False
    assert _connectivity(client, headers, org.id, reg["device_id"])["ssh_auth"] == "key"

    # The agent installed the CA line and says so on its next registration.
    _register_wg(client, reg["device_token"], WG_KEY_A, ssh_ca_public_key=body["ssh_ca_public_key"] + " comment")
    assert _connectivity(client, headers, org.id, reg["device_id"])["ssh_auth"] == "ca"

    # A device trusting some other CA (rotated, or a different server) is not counted.
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "other")], check=True)
    other = (tmp_path / "other.pub").read_text()
    assert _register_wg(client, reg["device_token"], WG_KEY_A, ssh_ca_public_key=other).status_code == 200
    assert _connectivity(client, headers, org.id, reg["device_id"])["ssh_auth"] == "key"


def test_shell_session_uses_a_certificate_for_ca_devices(
    client: TestClient, db_session: Session, ca_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[ShellCertificate | None] = []
    original = EchoShellProvider.connect

    def connect(self, **kwargs):
        seen.append(kwargs.get("certificate"))
        return original(self, **kwargs)

    monkeypatch.setattr(EchoShellProvider, "connect", connect)
    org, headers, reg = _device(client, db_session, name="pi-cert", mac="aa:bb:cc:dd:02:02")
    _register_wg(client, reg["device_token"], WG_KEY_A, ssh_ca_public_key=ca_settings.shell_ssh_ca_public_key)
    _report_handshake(client, WG_KEY_A)
    created = client.post(f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions", headers=headers)
    assert created.status_code == 201, created.text
    with client.websocket_connect(created.json()["websocket_path"] + f"?ticket={created.json()['ticket']}") as ws:
        ws.receive_bytes()
        ws.send_text("exit\n")
        ws.receive_bytes()
    assert seen[0] is not None
    assert seen[0].principal == ssh_principal(reg["device_id"])
    assert seen[0].key_id.startswith(f"shell:{created.json()['session_id']}:")


def test_retired_gateway_key_is_not_sent_and_key_devices_are_refused(
    client: TestClient, db_session: Session, ca_settings: Settings
) -> None:
    retired = ca_settings.model_copy(
        update={"shell_gateway_key_retired": True, "shell_gateway_public_key": "ssh-ed25519 AAAAgateway"}
    )
    client.app.dependency_overrides[get_settings] = lambda: retired
    org, headers, reg = _device(client, db_session, name="pi-old", mac="aa:bb:cc:dd:02:03")
    body = _register_wg(client, reg["device_token"], WG_KEY_A).json()
    assert body["ssh_gateway_public_key"] is None
    assert body["ssh_gateway_key_retired"] is True
    _report_handshake(client, WG_KEY_A)
    created = client.post(
        f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/shell-sessions", headers=headers
    ).json()
    with client.websocket_connect(created["websocket_path"] + f"?ticket={created['ticket']}") as ws:
        assert "certificate authority" in ws.receive_text()


# --- meteorcloud-ssh ---------------------------------------------------------------


def test_cli_signs_only_for_ca_devices_and_audits(
    client: TestClient,
    db_session: Session,
    ca_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from app.cli import main as cli

    monkeypatch.setattr(cli, "SessionLocal", lambda: Session(db_session.get_bind()))
    monkeypatch.setattr(cli, "get_settings", lambda: ca_settings)
    operator = tmp_path / "op"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(operator)], check=True)
    public_key = operator.with_name("op.pub").read_text()

    _org, _headers, reg = _device(client, db_session, name="pi-cli-ca", mac="aa:bb:cc:dd:02:04")
    _register_wg(client, reg["device_token"], WG_KEY_A)
    db_session.commit()
    assert cli.sign_ssh_cert(reg["device_id"], "ubuntu@host", public_key) == 3  # offline
    _report_handshake(client, WG_KEY_A)
    assert cli.sign_ssh_cert(reg["device_id"], "ubuntu@host", public_key) == 4  # still on the shared key
    assert cli.sign_ssh_cert(reg["device_id"], "ubuntu@host", "not a key") == 2
    capsys.readouterr()

    _register_wg(client, reg["device_token"], WG_KEY_A, ssh_ca_public_key=ca_settings.shell_ssh_ca_public_key)
    assert cli.shell_target(reg["device_id"]) == 0
    assert capsys.readouterr().out.strip().endswith("\tca")
    assert cli.sign_ssh_cert(reg["device_id"], "ubuntu@host", public_key) == 0
    cert = tmp_path / "op-cert.pub"
    cert.write_text(capsys.readouterr().out)
    details = _certificate_details(cert)
    assert ssh_principal(reg["device_id"]) in details
    assert "cli:ubuntu@host" in details
    db_session.expire_all()
    assert db_session.query(AuditEvent).filter_by(action="device.shell.certificate").count() == 1
