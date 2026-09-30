"""MQTT request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class MqttConnectInfo(BaseModel):
    host: str
    port: int
    username: str
    password: str
    tls: bool = True
    ca_cert: str | None = None


class MqttAuthRequest(BaseModel):
    username: str = ""
    password: str = ""


class MqttAuthResponse(BaseModel):
    result: Literal["allow", "deny"]
    is_superuser: bool = False


class MqttAuthorizeRequest(BaseModel):
    username: str = ""
    topic: str = ""
    action: str = ""


class MqttAuthorizeResponse(BaseModel):
    result: Literal["allow", "deny"]


class DevicePingResponse(BaseModel):
    command_id: uuid.UUID
    status: str
    round_trip_ms: int | None = None
    result: dict[str, Any] | None = None
    message: str | None = None


class DeviceCommandResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    type: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    result: dict[str, Any] | None = None


class PingCommandPayload(BaseModel):
    command_id: uuid.UUID
    type: Literal["ping"] = "ping"


class MqttTestPublishRequest(BaseModel):
    topic: str | None = None
    device_id: uuid.UUID | None = None
    payload: str | dict[str, Any] | None = None


class MqttTestPublishResponse(BaseModel):
    topic: str
    payload: str
