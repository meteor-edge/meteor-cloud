"""Connectivity request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentWireGuardRegisterRequest(BaseModel):
    public_key: str = Field(min_length=40, max_length=64)

    @field_validator("public_key")
    @classmethod
    def _strip_key(cls, value: str) -> str:
        return value.strip()


class AgentWireGuardRegisterResponse(BaseModel):
    status: str
    provider: str
    wg_address: str
    server_public_key: str
    server_endpoint: str
    allowed_ips: str
    persistent_keepalive: int
    ssh_user: str
    ssh_gateway_public_key: str | None = None
    client_config: str


class DeviceConnectivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID
    organization_id: uuid.UUID
    provider: str
    wg_public_key: str | None
    wg_address: str | None
    status: str
    last_handshake_at: datetime | None
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
