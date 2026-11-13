"""rules, rule_versions, alerts, alert_events, alert_silences, notification_integrations.

Revision ID: 0013_rules
Revises: 0012_applications
Create Date: 2026-11-13 00:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_rules"
down_revision: str | Sequence[str] | None = "0012_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALL = ("owner", "admin")
# (resource, action, label, description, system roles that get it)
PERMISSIONS = (
    (
        "rule",
        "read",
        "View rules",
        "List rules, their versions and matching devices.",
        (*_ALL, "operator", "developer", "viewer"),
    ),
    ("rule", "create", "Create rules", "Create alerting and automation rules.", (*_ALL, "developer")),
    (
        "rule",
        "update",
        "Update rules",
        "Change, enable and disable rules. Every save is a new version.",
        (*_ALL, "developer"),
    ),
    ("rule", "delete", "Delete rules", "Delete rules. Their alert history stays.", (*_ALL, "developer")),
    (
        "alert",
        "read",
        "View alerts",
        "List alerts, their history and silences.",
        (*_ALL, "operator", "developer", "viewer"),
    ),
    (
        "alert",
        "acknowledge",
        "Acknowledge alerts",
        "Mark firing alerts as acknowledged.",
        (*_ALL, "operator", "developer"),
    ),
    (
        "alert",
        "silence",
        "Silence alerts",
        "Hold notifications for matching alerts for a while.",
        (*_ALL, "operator", "developer"),
    ),
    (
        "integration",
        "manage",
        "Manage notification integrations",
        "Add, change and remove the webhooks rules deliver to, including their signing secrets.",
        (*_ALL, "developer"),
    ),
)


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _fk(table: str, ondelete: str = "CASCADE") -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete=ondelete)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("organization_id", _uuid(), _fk("organizations"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("created_by_user_id", _uuid(), _fk("users", "SET NULL"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_rules_organization_id", "rules", ["organization_id"])

    op.create_table(
        "rule_versions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("rule_id", _uuid(), _fk("rules"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_user_id", _uuid(), _fk("users", "SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("rule_id", "version", name="uq_rule_versions_rule_version"),
    )

    op.create_table(
        "alerts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("organization_id", _uuid(), _fk("organizations"), nullable=False),
        sa.Column("rule_id", _uuid(), _fk("rules"), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("rule_name", sa.String(200), nullable=False),
        sa.Column("device_id", _uuid(), _fk("devices"), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), server_default="", nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("silenced", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", _uuid(), _fk("users", "SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolve_reason", sa.String(32), nullable=True),
        sa.Column("trigger_event_id", sa.String(64), nullable=True),
        sa.Column("trigger_values", postgresql.JSONB(), server_default="{}", nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_alerts_org_fired", "alerts", ["organization_id", "fired_at"])
    op.create_index("ix_alerts_rule_id", "alerts", ["rule_id"])
    op.create_index("ix_alerts_device_id", "alerts", ["device_id"])
    op.create_index(
        "uq_alerts_active",
        "alerts",
        ["rule_id", "device_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved'"),
    )

    op.create_table(
        "alert_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("alert_id", _uuid(), _fk("alerts"), nullable=False),
        sa.Column("transition", sa.String(16), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("event_id", sa.String(64), nullable=True),
        sa.Column("values", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_alert_events_idempotency_key"),
    )
    op.create_index("ix_alert_events_alert_id", "alert_events", ["alert_id", "at"])

    op.create_table(
        "alert_silences",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("organization_id", _uuid(), _fk("organizations"), nullable=False),
        sa.Column("rule_id", _uuid(), _fk("rules"), nullable=True),
        sa.Column("device_id", _uuid(), _fk("devices"), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", _uuid(), _fk("users", "SET NULL"), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_alert_silences_org_ends", "alert_silences", ["organization_id", "ends_at"])

    op.create_table(
        "notification_integrations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("organization_id", _uuid(), _fk("organizations"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("secret_sealed", sa.LargeBinary(), nullable=True),
        sa.Column("created_by_user_id", _uuid(), _fk("users", "SET NULL"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("organization_id", "name", name="uq_notification_integrations_org_name"),
    )
    op.create_index("ix_notification_integrations_organization_id", "notification_integrations", ["organization_id"])

    bind = op.get_bind()
    for resource, action, label, description, roles in PERMISSIONS:
        permission_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, label, description) "
                "VALUES (:id, :resource, :action, :label, :description)"
            ),
            {"id": permission_id, "resource": resource, "action": action, "label": label, "description": description},
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT id, :permission_id FROM roles WHERE is_system AND organization_id IS NULL AND key IN :keys"
            ).bindparams(sa.bindparam("keys", expanding=True)),
            {"permission_id": permission_id, "keys": list(roles)},
        )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE resource IN ('rule', 'alert', 'integration')")
    op.drop_table("notification_integrations")
    op.drop_table("alert_silences")
    op.drop_table("alert_events")
    op.drop_table("alerts")
    op.drop_table("rule_versions")
    op.drop_table("rules")
