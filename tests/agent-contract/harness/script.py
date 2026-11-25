"""Scripted control-plane answers shared by the scenarios (all schema-valid)."""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from harness.fake_cp import Reply

ORG_ID = "5d1c7a4e-3b0f-4c3a-9a51-7c1f00000001"


def ssh_key(label: str, comment: str = "") -> str:
    """A well-formed ssh-ed25519 public key (the server checks the blob names its type)."""
    kind = b"ssh-ed25519"
    raw = hashlib.sha256(label.encode()).digest()
    blob = len(kind).to_bytes(4, "big") + kind + len(raw).to_bytes(4, "big") + raw
    return f"ssh-ed25519 {base64.b64encode(blob).decode()}" + (f" {comment}" if comment else "")


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def registered(contract: Any, device_id: str, *, mqtt: dict[str, Any] | None = None, token: str = "device_token") -> Reply:
    return Reply(201, {
        "device_id": device_id,
        "device_token": contract.secret(token),
        "organization_id": ORG_ID,
        "name": "contract-device",
        "heartbeat_interval_seconds": 60,
        "mqtt": mqtt,
    })


def heartbeat(device_id: str, **flags: bool) -> Reply:
    return Reply(200, {
        "device_id": device_id,
        "status": "online",
        "heartbeat_interval_seconds": 60,
        "server_time": now(),
        "desired_software": [],
        "wifi_pending": flags.get("wifi_pending", False),
        "actions_pending": flags.get("actions_pending", False),
    })


def accepted(count: int = 0) -> Reply:
    return Reply(200, {"accepted": count})


def error(status: int, code: str, message: str = "", **headers: str) -> Reply:
    return Reply(status, {"error": {"code": code, "message": message or code}}, dict(headers))


def register_and_basics(contract: Any, *, mqtt: dict[str, Any] | None = None) -> str:
    """Registration plus quiet answers for everything a run cycle asks; returns the device id."""
    device_id = str(uuid.uuid4())
    contract.script("POST", "/api/v1/agent/register", registered(contract, device_id, mqtt=mqtt))
    contract.script("POST", "/api/v1/agent/connectivity/wireguard",
                    error(404, "connectivity_disabled", "Device connectivity is disabled for this deployment."))
    contract.script("GET", "/api/v1/agent/mqtt", Reply(200, {"enabled": False, "config_version": None}))
    contract.script("GET", "/api/v1/agent/software", Reply(200, []))
    contract.script("POST", "/api/v1/agent/network/wifi/report", accepted())
    contract.script("POST", "/api/v1/agent/heartbeat", heartbeat(device_id))
    contract.script("GET", "/api/v1/health", Reply(200, {"status": "ok", "service": "meteorcloud", "version": "test"}))
    return device_id
