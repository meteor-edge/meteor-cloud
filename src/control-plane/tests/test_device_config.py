"""The first-boot settings file the console downloads: the same file Meteor Imager writes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.shacrypt import sha512_crypt
from app.devices.device_config import DeviceConfigRequest, render_device_config
from tests.conftest import auth_header, create_org_with_owner, create_user

SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyOnlyForTests user@laptop"
PASSWORD_HASH = sha512_crypt("Hello world!", salt="saltstring")

FULL = {
    "enrollment": {"domain": "fleet.example.com:8443", "api_key": "key_secret-value", "device_name": "Lobby kiosk"},
    "hostname": "pi-lab-01",
    "timezone": "Europe/Berlin",
    "wifi": {"ssid": 'My Net; \\ "x"', "psk": "wifi-password-123", "country": "DE"},
    "user": {"name": "pi", "password": "Hello world!"},
    "ssh": {"enabled": True, "password_authentication": False, "authorized_key": SSH_KEY},
}

# Byte for byte what the agent's first-boot test applies (CONSOLE_SETTINGS_FILE in
# src/device-plane/agent/tests/test_firstboot_packaging.py) and Meteor Imager's MeteorEnvV2 writes.
EXPECTED = (
    "METEOR_ENV_VERSION=2\n"
    "METEOR_DOMAIN=fleet.example.com:8443\n"
    "METEOR_API_KEY=key_secret-value\n"
    "METEOR_NAME=Lobby kiosk\n"
    "METEOR_HOSTNAME=pi-lab-01\n"
    "METEOR_TIMEZONE=Europe/Berlin\n"
    'METEOR_WIFI_SSID=My Net; \\ "x"\n'
    "METEOR_WIFI_PSK=wifi-password-123\n"
    "METEOR_WIFI_COUNTRY=DE\n"
    "METEOR_WIFI_HIDDEN=false\n"
    "METEOR_USER=pi\n"
    "METEOR_USER_PASSWORD_HASH=$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1\n"
    "METEOR_SSH_ENABLED=true\n"
    "METEOR_SSH_PASSWORD_AUTH=false\n"
    f"METEOR_SSH_AUTHORIZED_KEY={SSH_KEY}\n"
)


def test_full_file_matches_the_imagers() -> None:
    assert render_device_config(DeviceConfigRequest.model_validate(FULL), password_hash=PASSWORD_HASH) == EXPECTED


def test_only_what_was_set_is_written() -> None:
    config = DeviceConfigRequest.model_validate(
        {"enrollment": {"domain": "192.168.0.10:8000", "api_key": "key_abcdefgh"}, "ssh": {"authorized_key": SSH_KEY}}
    )
    assert render_device_config(config) == (
        "METEOR_ENV_VERSION=2\n"
        "METEOR_DOMAIN=192.168.0.10:8000\n"
        "METEOR_API_KEY=key_abcdefgh\n"
        f"METEOR_SSH_AUTHORIZED_KEY={SSH_KEY}\n"
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ssh": {}},
        {"enrollment": {"domain": "https://fleet.example.com", "api_key": "key_abcdefgh"}},
        {"enrollment": {"domain": "localhost:8000", "api_key": "key_abcdefgh"}},
        {"enrollment": {"domain": "fleet.example.com", "api_key": "not-a-key"}},
        {"enrollment": {"domain": "fleet.example.com", "api_key": "key_abcdefgh", "device_name": "a\nMETEOR_USER=x"}},
        {"hostname": "Not_A_Host"},
        {"hostname": "pi\nMETEOR_API_KEY=key_stolen"},
        {"timezone": "Mars/Olympus"},
        {"wifi": {"ssid": "net", "psk": "short", "country": "DE"}},
        {"wifi": {"ssid": "net", "psk": "long-enough", "country": "de"}},
        {"wifi": {"ssid": "net\nMETEOR_USER=x", "psk": "long-enough", "country": "DE"}},
        {"wifi": {"ssid": "x" * 33, "psk": "long-enough", "country": "DE"}},
        {"user": {"name": "root"}},
        {"user": {"name": "Ops"}},
        {"ssh": {"authorized_key": "ssh-ed25519 AAAA\nMETEOR_USER=x"}},
        {"ssh": {"authorized_key": "-----BEGIN OPENSSH PRIVATE KEY-----"}},
    ],
)
def test_invalid_settings_are_refused(body: dict) -> None:
    with pytest.raises(ValueError):
        DeviceConfigRequest.model_validate(body)


def test_download_needs_a_member_who_can_add_devices_and_never_echoes_secrets(
    client: TestClient, db_session: Session
) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    create_user(db_session, email="outsider@example.com")
    url = f"/api/v1/organizations/{org.id}/device-config"

    assert client.post(url, json=FULL).status_code == 401
    assert client.post(url, headers=auth_header(client, "outsider@example.com"), json=FULL).status_code == 404

    headers = auth_header(client, "owner@example.com")
    response = client.post(url, headers=headers, json=FULL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["file_name"] == "meteorenv.txt"
    lines = body["content"].splitlines()
    assert "METEOR_WIFI_PSK=wifi-password-123" in lines
    assert "Hello world!" not in body["content"]
    [hash_line] = [line for line in lines if line.startswith("METEOR_USER_PASSWORD_HASH=")]
    stored = hash_line.split("=", 1)[1]
    assert stored == sha512_crypt("Hello world!", salt=stored.split("$")[2])

    bad = client.post(url, headers=headers, json={**FULL, "wifi": {"ssid": "n", "psk": "bad\nsecret-psk", "country": "DE"}})
    assert bad.status_code == 422
    assert "secret-psk" not in bad.text
