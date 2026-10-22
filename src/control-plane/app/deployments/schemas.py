"""Deploy request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.releases.kinds import ReleaseKind


class DeployTarget(BaseModel):
    device_ids: list[uuid.UUID] = Field(default_factory=list)
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    device_type_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_some_target(self) -> DeployTarget:
        if not self.device_ids and not self.group_ids and not self.device_type_ids:
            raise ValueError("Select at least one device, group, or device type")
        return self


class DeployRollout(BaseModel):
    strategy: str = "immediate"
    batch_size: int | None = Field(default=None, ge=1, le=10_000)
    delay_seconds: int | None = Field(default=None, ge=0, le=86_400)

    @model_validator(mode="after")
    def _validate_strategy(self) -> DeployRollout:
        strategy = (self.strategy or "immediate").strip().lower()
        if strategy not in {"immediate", "batched"}:
            raise ValueError("rollout.strategy must be 'immediate' or 'batched'")
        self.strategy = strategy
        if strategy == "batched" and self.batch_size is None:
            raise ValueError("batched rollout requires batch_size")
        if strategy == "immediate":
            self.batch_size = None
            self.delay_seconds = None
        return self


class EndpointSpec(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    protocol: str = "http"

    @field_validator("hostname")
    @classmethod
    def _hostname(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, value: str) -> str:
        protocol = value.strip().lower()
        if protocol not in {"http", "https"}:
            raise ValueError("protocol must be http or https")
        return protocol


class DeploymentCreate(BaseModel):
    release_id: uuid.UUID
    target: DeployTarget
    rollout: DeployRollout = Field(default_factory=DeployRollout)
    name: str | None = Field(default=None, max_length=255)
    endpoint: EndpointSpec | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ReleaseBrief(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    kind: ReleaseKind


class DeploymentDeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    deployment_id: uuid.UUID
    device_id: uuid.UUID
    device_name: str | None = None
    status: str
    progress_percent: int | None = None
    previous_desired_release_id: uuid.UUID | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class DeploymentCounts(BaseModel):
    pending: int = 0
    queued: int = 0
    updating: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    skipped: int = 0
    total: int = 0


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    release_id: uuid.UUID
    release: ReleaseBrief | None = None
    name: str | None
    target: dict[str, Any]
    rollout: dict[str, Any]
    status: str
    provider: str = "native"
    provider_ref: str | None = None
    counts: DeploymentCounts = Field(default_factory=DeploymentCounts)
    created_by_user_id: uuid.UUID | None
    rollback_of_deployment_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    devices: list[DeploymentDeviceResponse] | None = None
