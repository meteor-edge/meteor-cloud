"""Fleet request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.devices.status import ConnectivityStatus
from app.mqtt.schemas import MqttConnectInfo


def _strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


# --------------------------------------------------------------------------- #
# Device types
# --------------------------------------------------------------------------- #
class DeviceTypeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    capabilities: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class DeviceTypeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    capabilities: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class DeviceTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    capabilities: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Device groups
# --------------------------------------------------------------------------- #
class DeviceGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    labels: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class DeviceGroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    labels: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class DeviceGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    labels: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Registration tokens
# --------------------------------------------------------------------------- #
class RegistrationTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    device_type_id: uuid.UUID | None = None
    device_group_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned


class RegistrationTokenResponse(BaseModel):
    """Registration-token metadata. Never includes the plaintext secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    token_prefix: str
    device_type_id: uuid.UUID | None
    device_group_id: uuid.UUID | None
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    revoked_at: datetime | None
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class RegistrationTokenCreateResponse(RegistrationTokenResponse):
    """Returned only at creation time; includes the one-time plaintext token."""

    token: str


# --------------------------------------------------------------------------- #
# Devices (admin)
# --------------------------------------------------------------------------- #
class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    device_type_id: uuid.UUID | None
    device_group_id: uuid.UUID | None
    is_enabled: bool
    status: ConnectivityStatus
    serial_number: str | None
    mac_addresses: list[str]
    hostname: str | None
    os_name: str | None
    os_version: str | None
    kernel_version: str | None
    architecture: str | None
    cpu_model: str | None
    cpu_cores: int | None
    memory_mb: int | None
    labels: dict[str, Any]
    metadata: dict[str, Any]
    credential_prefix: str | None
    last_seen_at: datetime | None
    registered_at: datetime | None
    created_at: datetime
    updated_at: datetime
    mqtt_configured: bool = False
    mqtt_status: str | None = None
    mqtt_status_at: datetime | None = None
    mqtt_metrics: dict[str, Any] | None = None
    mqtt_metrics_at: datetime | None = None


class DeviceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    device_type_id: uuid.UUID | None = None
    device_group_id: uuid.UUID | None = None
    labels: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    # Explicit flags so the caller can clear an assignment.
    clear_device_type: bool = False
    clear_device_group: bool = False

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned


DeviceSortField = Literal["name", "last_seen_at", "created_at", "registered_at"]
SortOrder = Literal["asc", "desc"]


class Page[T](BaseModel):
    """Typed pagination envelope."""

    items: list[T]
    total: int
    page: int
    page_size: int


class DeviceCredentialResponse(BaseModel):
    """Returned when a device credential is rotated by an admin."""

    device_id: uuid.UUID
    token: str
    credential_prefix: str


# --------------------------------------------------------------------------- #
# Agent API (device-facing)
# --------------------------------------------------------------------------- #
class AgentRegisterRequest(BaseModel):
    token: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=255)
    serial_number: str | None = Field(default=None, max_length=255)
    mac_addresses: list[str] = Field(default_factory=list)
    hostname: str | None = Field(default=None, max_length=255)
    os_name: str | None = Field(default=None, max_length=255)
    os_version: str | None = Field(default=None, max_length=255)
    kernel_version: str | None = Field(default=None, max_length=255)
    architecture: str | None = Field(default=None, max_length=64)
    cpu_model: str | None = Field(default=None, max_length=255)
    cpu_cores: int | None = Field(default=None, ge=0)
    memory_mb: int | None = Field(default=None, ge=0)
    labels: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRegisterResponse(BaseModel):
    device_id: uuid.UUID
    device_token: str
    organization_id: uuid.UUID
    name: str
    heartbeat_interval_seconds: int
    mqtt: MqttConnectInfo | None = None


class AgentHeartbeatRequest(BaseModel):
    hostname: str | None = Field(default=None, max_length=255)
    os_version: str | None = Field(default=None, max_length=255)
    kernel_version: str | None = Field(default=None, max_length=255)
    metrics: dict[str, Any] = Field(default_factory=dict)


class AgentHeartbeatResponse(BaseModel):
    device_id: uuid.UUID
    status: ConnectivityStatus
    heartbeat_interval_seconds: int
    server_time: datetime


# --------------------------------------------------------------------------- #
# Enrollment API keys (admin)
# --------------------------------------------------------------------------- #
class EnrollmentApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned


class EnrollmentApiKeyResponse(BaseModel):
    """API-key metadata. Never includes the plaintext secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    key_prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class EnrollmentApiKeyCreateResponse(EnrollmentApiKeyResponse):
    """Returned only at creation time; includes the one-time plaintext key."""

    api_key: str


# --------------------------------------------------------------------------- #
# Device enrollment requests (admin)
# --------------------------------------------------------------------------- #
EnrollmentStatus = Literal["pending", "approved", "rejected", "expired"]


class DeviceEnrollmentRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    status: str
    claim_secret_prefix: str
    requested_name: str | None
    assigned_name: str | None
    device_type_id: uuid.UUID | None
    device_group_id: uuid.UUID | None
    serial_number: str | None
    mac_addresses: list[str]
    hostname: str | None
    os_name: str | None
    os_version: str | None
    kernel_version: str | None
    architecture: str | None
    cpu_model: str | None
    cpu_cores: int | None
    memory_mb: int | None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    claimed_at: datetime | None
    device_id: uuid.UUID | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EnrollmentApproveRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    device_type_id: uuid.UUID | None = None
    device_group_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class EnrollmentRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str | None) -> str | None:
        return _strip_optional(value)


# --------------------------------------------------------------------------- #
# Agent enrollment API (device-facing, API-key authenticated)
# --------------------------------------------------------------------------- #
class AgentEnrollRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    serial_number: str | None = Field(default=None, max_length=255)
    mac_addresses: list[str] = Field(default_factory=list)
    hostname: str | None = Field(default=None, max_length=255)
    os_name: str | None = Field(default=None, max_length=255)
    os_version: str | None = Field(default=None, max_length=255)
    kernel_version: str | None = Field(default=None, max_length=255)
    architecture: str | None = Field(default=None, max_length=64)
    cpu_model: str | None = Field(default=None, max_length=255)
    cpu_cores: int | None = Field(default=None, ge=0)
    memory_mb: int | None = Field(default=None, ge=0)
    labels: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEnrollResponse(BaseModel):
    request_id: uuid.UUID
    claim_secret: str
    status: EnrollmentStatus
    poll_interval_seconds: int
    expires_at: datetime | None


class AgentEnrollCheckResponse(BaseModel):
    ok: bool
    organization_id: uuid.UUID
    organization_name: str
    key_name: str
    key_prefix: str
    expires_at: datetime | None


class AgentEnrollPollRequest(BaseModel):
    request_id: uuid.UUID
    claim_secret: str = Field(min_length=1)


class AgentEnrollPollResponse(BaseModel):
    status: EnrollmentStatus
    poll_interval_seconds: int
    # Present only once, when an approved request is first claimed.
    device_id: uuid.UUID | None = None
    device_token: str | None = None
    organization_id: uuid.UUID | None = None
    name: str | None = None
    heartbeat_interval_seconds: int | None = None
    mqtt: MqttConnectInfo | None = None
    rejection_reason: str | None = None
