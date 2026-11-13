"""Request and response models for rules, alerts, silences and notification integrations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class RuleWrite(BaseModel):
    """A full rule definition (contracts/rules-v1.md). Every save creates a new version."""

    definition: dict[str, Any]


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None
    name: str
    enabled: bool
    current_version: int
    scope_type: str
    severity: str
    definition: dict[str, Any]
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class RuleVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    definition: dict[str, Any]
    created_by_user_id: uuid.UUID | None
    created_at: datetime


class ProblemResponse(BaseModel):
    path: str
    message: str


class ValidationResult(BaseModel):
    valid: bool
    problems: list[ProblemResponse] = Field(default_factory=list)
    condition_hash: str | None = None


class MatchingDevice(BaseModel):
    id: uuid.UUID
    name: str
    device_group_id: uuid.UUID | None
    device_type_id: uuid.UUID | None


class DevicePreview(BaseModel):
    total: int
    devices: list[MatchingDevice]


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    rule_id: uuid.UUID
    rule_version: int
    rule_name: str
    device_id: uuid.UUID
    episode: int
    fingerprint: str
    severity: str
    status: str
    silenced: bool
    fired_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by_user_id: uuid.UUID | None
    resolved_at: datetime | None
    resolve_reason: str | None
    trigger_event_id: str | None
    trigger_values: dict[str, Any]


class AlertEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transition: str
    actor_type: str
    actor_id: str | None
    at: datetime
    reason: str | None
    event_id: str | None
    values: dict[str, Any]


class AlertDetail(AlertResponse):
    events: list[AlertEventResponse]


class SilenceCreate(BaseModel):
    rule_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    comment: str | None = Field(default=None, max_length=2000)
    starts_at: datetime | None = None
    ends_at: datetime

    @model_validator(mode="after")
    def _window(self) -> SilenceCreate:
        if self.starts_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class SilenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    rule_id: uuid.UUID | None
    device_id: uuid.UUID | None
    comment: str | None
    starts_at: datetime
    ends_at: datetime
    created_by_user_id: uuid.UUID | None
    created_at: datetime


class IntegrationCreate(BaseModel):
    kind: Literal["webhook", "slack"] = "webhook"
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    enabled: bool = True
    secret: str | None = Field(default=None, min_length=16, max_length=512)


class IntegrationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: HttpUrl | None = None
    enabled: bool | None = None
    # "" removes the secret; omitted keeps it.
    secret: str | None = Field(default=None, max_length=512)


class IntegrationResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    kind: str
    name: str
    url: str
    enabled: bool
    has_secret: bool
    created_at: datetime
    updated_at: datetime


# ---- internal (engine and dispatcher) ------------------------------------


class SnapshotRule(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    version: int
    definition: dict[str, Any]


class SnapshotDevice(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    group_id: uuid.UUID | None
    device_type_id: uuid.UUID | None
    labels: dict[str, str]


class RulesSnapshot(BaseModel):
    version: str
    rules: list[SnapshotRule]
    devices: list[SnapshotDevice]


class TransitionEvent(BaseModel):
    transition: Literal["firing", "resolved"]
    episode: int = Field(ge=0)
    at: datetime
    event_id: str | None = Field(default=None, max_length=64)
    values: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str = Field(default="", max_length=64)
    reason: str | None = Field(default=None, max_length=32)


class AlertTransition(BaseModel):
    """One ``alert`` action from the engine's outbox (contracts/rules-v1.md, Outbox payload)."""

    idempotency_key: str = Field(min_length=1, max_length=255)
    rule_id: uuid.UUID
    rule_version: int
    rule_name: str = Field(max_length=200)
    organization_id: uuid.UUID
    device_id: uuid.UUID
    severity: Literal["info", "warning", "critical"]
    event: TransitionEvent


class AlertTransitionBatch(BaseModel):
    items: list[AlertTransition] = Field(min_length=1, max_length=500)


class AlertTransitionResult(BaseModel):
    idempotency_key: str
    alert_id: uuid.UUID | None
    status: Literal["applied", "duplicate", "ignored"]
    silenced: bool = False
    detail: str | None = None


class AlertTransitionResponse(BaseModel):
    results: list[AlertTransitionResult]


class EngineAction(BaseModel):
    """Any outbox action as the dispatcher sends it: the outbox payload plus its key."""

    idempotency_key: str = Field(min_length=1, max_length=255)
    rule_id: uuid.UUID
    rule_version: int
    rule_name: str = Field(max_length=200)
    organization_id: uuid.UUID
    device_id: uuid.UUID
    severity: Literal["info", "warning", "critical"]
    event: TransitionEvent
    action: dict[str, Any] = Field(default_factory=dict)


class CommandResult(BaseModel):
    status: Literal["sent", "skipped"]
    detail: str | None = None


class DeliveryRequest(BaseModel):
    integration_id: uuid.UUID
    organization_id: uuid.UUID
    rule_id: uuid.UUID
    device_id: uuid.UUID
    at: datetime


class DeliveryResponse(BaseModel):
    url: str
    kind: str
    secret: str
    enabled: bool
    silenced: bool


class RuleTestEvent(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    time: datetime
    stream: str | None = Field(default=None, max_length=128)
    data: dict[str, Any]


class RuleTestRequest(BaseModel):
    """Run a definition over sample events with a virtual clock. Nothing is stored, no action runs."""

    definition: dict[str, Any]
    events: list[RuleTestEvent] = Field(max_length=1000)
    until: datetime | None = None
