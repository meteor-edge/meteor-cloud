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


class RolloutPhase(BaseModel):
    """One phase of a phased rollout: a cumulative share of deployable targets."""

    count: int | None = Field(default=None, ge=1)
    percent: int | None = Field(default=None, ge=1, le=100)
    # Soak time after the previous phase became terminal.
    delay_seconds: int | None = Field(default=None, ge=0, le=604_800)

    @model_validator(mode="after")
    def _exactly_one_size(self) -> RolloutPhase:
        if (self.count is None) == (self.percent is None):
            raise ValueError("each phase needs exactly one of count or percent")
        return self


# Only valid with strategy "phased".
_PHASED_FIELDS = ("phases", "max_concurrent", "failure_threshold_percent", "pending_timeout_seconds")


class DeployRollout(BaseModel):
    strategy: str = "immediate"
    batch_size: int | None = Field(default=None, ge=1, le=10_000)
    delay_seconds: int | None = Field(default=None, ge=0, le=86_400)
    phases: list[RolloutPhase] | None = Field(default=None, min_length=1, max_length=10)
    max_concurrent: int | None = Field(default=None, ge=1, le=10_000)
    failure_threshold_percent: int | None = Field(default=None, ge=0, le=100)
    pending_timeout_seconds: int | None = Field(default=None, ge=60, le=2_592_000)

    @model_validator(mode="after")
    def _validate_strategy(self) -> DeployRollout:
        strategy = (self.strategy or "immediate").strip().lower()
        if strategy not in {"immediate", "batched", "phased"}:
            raise ValueError("rollout.strategy must be 'immediate', 'batched' or 'phased'")
        self.strategy = strategy
        if strategy != "phased":
            for name in _PHASED_FIELDS:
                if getattr(self, name) is not None:
                    raise ValueError(f"rollout.{name} is only supported with strategy 'phased'")
        if strategy == "batched" and self.batch_size is None:
            raise ValueError("batched rollout requires batch_size")
        if strategy == "immediate":
            self.batch_size = None
            self.delay_seconds = None
        if strategy == "phased":
            self._validate_phases()
        return self

    def _validate_phases(self) -> None:
        if self.batch_size is not None or self.delay_seconds is not None:
            raise ValueError(
                "phased rollout uses phases; batch_size and delay_seconds are not allowed"
            )
        if not self.phases:
            raise ValueError("phased rollout requires phases")
        if self.phases[0].delay_seconds is not None:
            raise ValueError("the first phase cannot have delay_seconds")
        if self.phases[-1].percent != 100:
            raise ValueError("the last phase must be percent 100")
        for field_name in ("count", "percent"):
            values = [getattr(phase, field_name) for phase in self.phases if getattr(phase, field_name) is not None]
            if any(later <= earlier for earlier, later in zip(values, values[1:], strict=False)):
                raise ValueError(f"phase {field_name} values must be strictly increasing")

    def to_record(self) -> dict[str, Any]:
        """What is stored on deployments.rollout; immediate and batched keep their old shape."""
        record = self.model_dump()
        if self.strategy != "phased":
            for name in _PHASED_FIELDS:
                record.pop(name)
        return record


class EndpointSpec(BaseModel):
    # Empty: the suggested <release>.<device>.<org>.<apps domain>.
    hostname: str | None = Field(default=None, max_length=255)
    port: int = Field(ge=1, le=65535)
    protocol: str = "http"

    @field_validator("hostname")
    @classmethod
    def _hostname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower() or None

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
    # Runtime health from the agent (healthy / unhealthy / unknown); separate from install status.
    health: str | None = None
    reported_at: datetime | None = None
    # When desired software was written for this device; null while queued or skipped.
    activated_at: datetime | None = None
    # Device.last_seen_at, so the console can tell "waiting for device" from "installing".
    device_last_seen_at: datetime | None = None
    # URL state, only when this Deploy created an endpoint for the device.
    endpoint_availability: str | None = None
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
    timed_out: int = 0
    total: int = 0


class RollbackSkippedDevice(BaseModel):
    device_id: uuid.UUID
    device_name: str
    reason: str


class RollbackResponse(BaseModel):
    """Rollback returns each device to its own previous release.

    Devices that had different previous releases get one Deploy per release; devices with no
    previous release (a first install) are left alone and listed in ``skipped``.
    """

    deployments: list[DeploymentResponse]
    skipped: list[RollbackSkippedDevice] = Field(default_factory=list)


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
