"""Request and response models for rules, alerts, silences and notification integrations."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, model_validator


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


class AlertListItem(AlertResponse):
    device_name: str


class AlertDetail(AlertListItem):
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


IntegrationKind = Literal["webhook", "slack", "email", "telegram"]
TELEGRAM_TOKEN = re.compile(r"^[0-9]{5,20}:[A-Za-z0-9_-]{20,100}\Z")
TELEGRAM_CHAT = re.compile(r"^(-?[0-9]{1,20}|@[A-Za-z0-9_]{4,64})\Z")
Recipients = Annotated[list[EmailStr], Field(max_length=20)]


def check_integration(kind: str, *, url: Any, recipients: Any, chat_id: str | None, secret: str | None) -> None:
    """What each kind needs, checked on the complete integration. Raises ValueError."""
    if kind in ("webhook", "slack"):
        if not url:
            raise ValueError("url is required")
        if recipients or chat_id:
            raise ValueError("recipients and chat_id do not apply to this kind")
        if secret and len(secret) < 16:
            raise ValueError("secret must be at least 16 characters")
        return
    if url:
        raise ValueError("url does not apply to this kind")
    if kind == "email":
        if not recipients:
            raise ValueError("at least one recipient is required")
        if chat_id or secret:
            raise ValueError("chat_id and secret do not apply to email")
        return
    if recipients:
        raise ValueError("recipients do not apply to telegram")
    if not chat_id or not TELEGRAM_CHAT.match(chat_id):
        raise ValueError("chat_id must be a number or @channelname")
    if not secret or not TELEGRAM_TOKEN.match(secret):
        raise ValueError("secret must be the bot token from @BotFather")


class IntegrationCreate(BaseModel):
    kind: IntegrationKind = "webhook"
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl | None = None
    recipients: Recipients | None = None
    chat_id: str | None = Field(default=None, max_length=80)
    enabled: bool = True
    # Signing secret (webhook, slack) or bot token (telegram).
    secret: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _kind(self) -> IntegrationCreate:
        check_integration(self.kind, url=self.url, recipients=self.recipients, chat_id=self.chat_id, secret=self.secret)
        return self


class IntegrationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: HttpUrl | None = None
    recipients: Recipients | None = None
    chat_id: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    # "" removes the secret; omitted keeps it.
    secret: str | None = Field(default=None, max_length=512)


class IntegrationResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    kind: str
    name: str
    url: str
    recipients: list[str]
    chat_id: str | None
    enabled: bool
    has_secret: bool
    created_at: datetime
    updated_at: datetime


class ChannelCapabilities(BaseModel):
    """Which channels can deliver on this platform now."""

    engine: bool
    email: bool


class DeliveryTestResult(BaseModel):
    delivered: bool
    error: str | None = None


# ---- metrics and live device state ----------------------------------------


class MetricResponse(BaseModel):
    field: str
    label: str
    unit: str
    group: str
    description: str
    suggested_operator: str
    suggested_value: float
    devices: int  # devices whose last message carries it
    min: float | None
    max: float | None


NumericOperator = Literal["gt", "gte", "lt", "lte", "eq", "ne", "between"]


class LiveCondition(BaseModel):
    field: str = Field(max_length=128)
    operator: NumericOperator = "gt"
    value: float = Field(allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _between(self) -> LiveCondition:
        if self.operator == "between" and (self.max is None or self.max < self.value):
            raise ValueError("between needs max, not less than value")
        return self


class LiveQuery(BaseModel):
    """Devices whose last readings match, whether or not a rule watches them."""

    conditions: list[LiveCondition] = Field(default_factory=list, max_length=10)
    match: Literal["all", "any"] = "all"
    search: str = Field(default="", max_length=200)
    state: Literal["any", "online", "offline"] = "any"
    alerts: Literal["any", "active", "none"] = "any"
    group_id: uuid.UUID | None = None
    device_type_id: uuid.UUID | None = None
    # Readings older than this do not count as "now".
    fresh_minutes: int = Field(default=5, ge=1, le=10080)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class LiveAlert(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: str
    severity: str
    status: str


class LiveDevice(BaseModel):
    id: uuid.UUID
    name: str
    device_group_id: uuid.UUID | None
    device_type_id: uuid.UUID | None
    online: bool
    metrics_at: datetime | None
    fresh: bool
    metrics: dict[str, float]
    alerts: list[LiveAlert]


class LiveResult(BaseModel):
    total: int  # devices in view
    matching: int
    devices: list[LiveDevice]


class AlertSummary(BaseModel):
    firing: int
    acknowledged: int
    devices: int  # devices with an active alert
    by_severity: dict[str, int]
    by_rule: dict[uuid.UUID, int]


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


class DeliveryDevice(BaseModel):
    device_id: uuid.UUID
    at: datetime


class DeliveryRequest(BaseModel):
    integration_id: uuid.UUID
    organization_id: uuid.UUID
    rule_id: uuid.UUID
    device_id: uuid.UUID
    at: datetime
    # A message for several devices of the rule: their names and silences in one call.
    devices: list[DeliveryDevice] = Field(default_factory=list, max_length=1000)


class DeliveryDeviceState(BaseModel):
    name: str
    silenced: bool


class DeliveryResponse(BaseModel):
    url: str
    kind: str
    secret: str
    enabled: bool
    silenced: bool
    config: dict[str, Any] = Field(default_factory=dict)
    device_name: str = ""
    organization_name: str = ""
    devices: list[DeliveryDeviceState] = Field(default_factory=list)  # in request order


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
