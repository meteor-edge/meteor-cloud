"""Connectivity request and response schemas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

SSH_HOST_KEY_TYPES = frozenset({"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"})


def normalize_ssh_host_key(value: str) -> str:
    """Return "<type> <base64>" (comment dropped) or raise ValueError."""
    parts = value.split()
    if len(parts) < 2 or parts[0] not in SSH_HOST_KEY_TYPES:
        raise ValueError("SSH host key must be an ssh-ed25519, ecdsa-sha2-nistp256 or ssh-rsa public key.")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("SSH host key is not valid base64.") from exc
    # The blob starts with the length-prefixed key type; it must match the declared type.
    if len(blob) < 4 or blob[4 : 4 + int.from_bytes(blob[:4], "big")] != parts[0].encode():
        raise ValueError("SSH host key type does not match its data.")
    return f"{parts[0]} {parts[1]}"


def ssh_host_key_fingerprint(key: str | None) -> str | None:
    """SHA256 fingerprint in ssh-keygen -l format."""
    if not key:
        return None
    blob = base64.b64decode(key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


class AgentWireGuardRegisterRequest(BaseModel):
    public_key: str = Field(min_length=40, max_length=64)
    ssh_user: str | None = Field(default=None, max_length=32)
    # Device sshd host public key (e.g. /etc/ssh/ssh_host_ed25519_key.pub). Never a private key.
    ssh_host_key: str | None = Field(default=None, max_length=2048)

    @field_validator("public_key")
    @classmethod
    def _strip_key(cls, value: str) -> str:
        return value.strip()

    @field_validator("ssh_user")
    @classmethod
    def _normalize_ssh_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        if not name:
            return None
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("Invalid SSH user.")
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Invalid SSH user.")
        return name

    @field_validator("ssh_host_key")
    @classmethod
    def _normalize_host_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_ssh_host_key(value)


class AgentWireGuardRegisterResponse(BaseModel):
    status: str
    provider: str
    wg_address: str
    server_public_key: str
    server_endpoint: str
    # What the device routes through the tunnel: the server's tunnel address.
    allowed_ips: str
    persistent_keepalive: int
    ssh_user: str
    ssh_gateway_public_key: str | None = None


class DeviceConnectivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID
    organization_id: uuid.UUID
    provider: str
    wg_public_key: str | None
    wg_address: str | None
    status: str
    last_handshake_at: datetime | None
    # True while the device's last WireGuard handshake is recent.
    connected: bool = False
    # connected (fresh handshake) | reconnecting (agent checks in, tunnel down) | offline | pending | revoked.
    # A handshake proves the tunnel only, not that SSH or an application works.
    state: str = "pending"
    agent_last_seen_at: datetime | None = None
    ssh_host_key_fingerprint: str | None = None
    last_ssh_ok_at: datetime | None = None
    last_ssh_error: str | None = None
    last_ssh_error_at: datetime | None = None
    # How to reach the device inside the tunnel: the server's tunnel IP and `ssh <user>@<wg_address>`.
    server_address: str = ""
    ssh_user: str = ""
    ssh_port: int = 22
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InternalPeer(BaseModel):
    device_id: uuid.UUID
    organization_id: uuid.UUID
    public_key: str
    allowed_ips: str
    status: str


class InternalPeerList(BaseModel):
    interface: str
    peers: list[InternalPeer]


class HandshakeReportItem(BaseModel):
    public_key: str
    last_handshake_at: datetime | None = None


class HandshakeReportRequest(BaseModel):
    items: list[HandshakeReportItem]
