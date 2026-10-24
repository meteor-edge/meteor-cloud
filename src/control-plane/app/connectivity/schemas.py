"""Connectivity request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentWireGuardRegisterRequest(BaseModel):
    public_key: str = Field(min_length=40, max_length=64)
    ssh_user: str | None = Field(default=None, max_length=32)

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
