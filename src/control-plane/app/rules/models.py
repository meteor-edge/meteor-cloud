"""Rules, their immutable versions, alerts and their history, silences, notification targets.

The data plane's rule engine evaluates rules (contracts/rules-v1.md); this module is the
source of truth for what a rule is and for the alert record people act on. The engine's
evaluation state lives in the data plane (PostgreSQL schema ``rules``), never here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin

ALERT_FIRING = "firing"
ALERT_ACKNOWLEDGED = "acknowledged"
ALERT_RESOLVED = "resolved"
ACTIVE_ALERT_STATUSES = (ALERT_FIRING, ALERT_ACKNOWLEDGED)


class Rule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A rule's identity and switches. Its definition lives in ``rule_versions``."""

    __tablename__ = "rules"
    __table_args__ = (Index("ix_rules_organization_id", "organization_id"),)

    # NULL only for platform-wide rules (platform administrators).
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuleVersion(Base, UUIDPrimaryKeyMixin):
    """One saved definition. Never updated: every save adds a version."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_rule_versions_rule_version"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class Alert(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One alert: opened by the engine, acknowledged by people, resolved by the engine."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_org_fired", "organization_id", "fired_at"),
        Index("ix_alerts_rule_id", "rule_id"),
        Index("ix_alerts_device_id", "device_id"),
        # At most one active alert per rule, device and fingerprint ("" in single mode).
        Index(
            "uq_alerts_active",
            "rule_id",
            "device_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("status <> 'resolved'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ALERT_FIRING)
    silenced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolve_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trigger_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class AlertEvent(Base, UUIDPrimaryKeyMixin):
    """One lifecycle transition of an alert. ``idempotency_key`` makes engine replays no-ops."""

    __tablename__ = "alert_events"
    __table_args__ = (
        Index("ix_alert_events_alert_id", "alert_id", "at"),
        UniqueConstraint("idempotency_key", name="uq_alert_events_idempotency_key"),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    transition: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # engine | user
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AlertSilence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Holds notifications for matching alerts in a time window. Alerts are still recorded."""

    __tablename__ = "alert_silences"
    __table_args__ = (Index("ix_alert_silences_org_ends", "organization_id", "ends_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class NotificationIntegration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Where webhook and notification actions deliver: a URL and an optional signing secret."""

    __tablename__ = "notification_integrations"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_notification_integrations_org_name"),
        Index("ix_notification_integrations_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # webhook | slack
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    # AES-GCM sealed (app.core.secretbox); never returned by the API.
    secret_sealed: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
