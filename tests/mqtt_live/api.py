"""Create isolated orgs/devices through the public API."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from mqtt_live.config import LiveConfig
from mqtt_live.http import request_json


@dataclass
class RegisteredDevice:
    device_id: str
    organization_id: str
    mqtt_username: str
    mqtt_password: str
    mqtt_host: str
    mqtt_port: int


class PlatformApi:
    def __init__(self, cfg: LiveConfig) -> None:
        self.cfg = cfg
        self.token: str | None = None
        self.organization_id: str | None = None

    def _url(self, path: str) -> str:
        return f"{self.cfg.platform_url}{path}"

    def register_unique_owner(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        email = f"mqtt-{suffix}@example.com"
        password = secrets.token_urlsafe(16)
        request_json(
            "POST",
            self._url("/api/v1/auth/register"),
            body={"email": email, "full_name": "MQTT Live", "password": password},
        )
        login = request_json(
            "POST",
            self._url("/api/v1/auth/login"),
            body={"email": email, "password": password},
        )
        self.token = login["access_token"]
        org = request_json(
            "POST",
            self._url("/api/v1/organizations"),
            token=self.token,
            body={"name": f"mqtt-live-{suffix}", "slug": f"mqtt-live-{suffix}"},
        )
        self.organization_id = org["id"]

    def register_device(self, name: str) -> RegisteredDevice:
        assert self.token and self.organization_id
        created = request_json(
            "POST",
            self._url(f"/api/v1/organizations/{self.organization_id}/registration-tokens"),
            token=self.token,
            body={"name": name, "max_uses": 1},
        )
        hex_id = uuid.uuid4().hex[:12]
        mac_address = ":".join(hex_id[i : i + 2] for i in range(0, 12, 2))
        body = request_json(
            "POST",
            self._url("/api/v1/agent/register"),
            body={"token": created["token"], "name": name, "mac_addresses": [mac_address]},
        )
        mqtt = body["mqtt"]
        return RegisteredDevice(
            device_id=body["device_id"],
            organization_id=body["organization_id"],
            mqtt_username=mqtt["username"],
            mqtt_password=mqtt["password"],
            mqtt_host=mqtt.get("host") or self.cfg.mqtt_host,
            mqtt_port=int(mqtt.get("port") or self.cfg.mqtt_port),
        )

    def get_device(self, device_id: str) -> dict:
        assert self.token and self.organization_id
        return request_json(
            "GET",
            self._url(f"/api/v1/organizations/{self.organization_id}/devices/{device_id}"),
            token=self.token,
        )

    def disable_device(self, device_id: str) -> None:
        assert self.token and self.organization_id
        request_json(
            "POST",
            self._url(f"/api/v1/organizations/{self.organization_id}/devices/{device_id}/disable"),
            token=self.token,
            body={},
        )

    def revoke_mqtt(self, device_id: str) -> None:
        assert self.token and self.organization_id
        request_json(
            "POST",
            self._url(
                f"/api/v1/organizations/{self.organization_id}/devices/{device_id}/mqtt/revoke"
            ),
            token=self.token,
            body={},
        )

    def ping(self, device_id: str) -> dict:
        assert self.token and self.organization_id
        return request_json(
            "POST",
            self._url(
                f"/api/v1/organizations/{self.organization_id}/devices/{device_id}/commands/ping"
            ),
            token=self.token,
            body={},
            timeout=60,
        )

    def delete_organization(self) -> None:
        if not self.token or not self.organization_id:
            return
        try:
            page = request_json(
                "GET",
                self._url(f"/api/v1/organizations/{self.organization_id}/devices?page_size=100"),
                token=self.token,
            )
            for item in (page or {}).get("items", []):
                try:
                    request_json(
                        "DELETE",
                        self._url(
                            f"/api/v1/organizations/{self.organization_id}/devices/{item['id']}"
                        ),
                        token=self.token,
                    )
                except Exception:
                    pass
            request_json(
                "DELETE",
                self._url(f"/api/v1/organizations/{self.organization_id}"),
                token=self.token,
            )
        except Exception:
            pass
