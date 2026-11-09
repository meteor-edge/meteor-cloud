"""Initial schema: every table, plus the permission catalog and system roles.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-09 00:00:00.000000

Squashed from the former 0001-0026 chain (no production installs existed). A database
created by that chain has a different head and must be dropped and recreated.

The catalog is frozen here rather than read from app.authorization: a migration must do
the same thing whenever it runs, whatever the application code says later. Change the
catalog with a new migration.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (resource, action, label, description)
PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("artifact", "create", "Upload artifacts", "Upload new artifact versions."),
    ("artifact", "delete", "Delete artifacts", "Delete artifacts and their stored objects."),
    ("artifact", "read", "View artifacts", "List and download artifacts."),
    ("artifact", "update", "Update artifacts", "Edit artifact metadata."),
    ("deployment", "cancel", "Cancel deployments", "Cancel in-progress deployments."),
    ("deployment", "create", "Create deployments", "Start deployments."),
    ("deployment", "read", "View deployments", "List and open deployments."),
    ("deployment", "rollback", "Rollback deployments", "Roll back a deployment."),
    ("device", "create", "Create devices", "Create registration tokens and approve enrollment."),
    ("device", "delete", "Delete devices", "Remove devices within scope."),
    ("device", "logs", "View device logs", "Read service, container and system logs on a device."),
    ("device", "read", "View devices", "List and open devices within scope."),
    ("device", "reboot", "Reboot devices", "Send reboot and similar operate commands within scope."),
    ("device", "shell", "Open device terminal", "Open a browser SSH session to a device."),
    ("device", "update", "Update devices", "Edit device metadata, type, group, and enablement."),
    ("device_group", "create", "Create device groups", "Create new device groups."),
    ("device_group", "delete", "Delete device groups", "Delete device groups within scope."),
    ("device_group", "read", "View device groups", "List and open device groups within scope."),
    ("device_group", "update", "Update device groups", "Edit device groups within scope."),
    ("device_type", "create", "Create device types", "Create hardware models."),
    ("device_type", "delete", "Delete device types", "Delete device types."),
    ("device_type", "read", "View device types", "List and open device types."),
    ("device_type", "update", "Update device types", "Edit device types."),
    ("endpoint", "create", "Create endpoints", "Register public application hostnames."),
    ("endpoint", "delete", "Delete endpoints", "Remove public application endpoints."),
    ("endpoint", "read", "View endpoints", "List and open public application endpoints."),
    ("endpoint", "update", "Update endpoints", "Change or disable public application endpoints."),
    ("enrollment_key", "manage", "Manage enrollment API keys", "Create and revoke enrollment API keys."),
    ("enrollment_key", "read", "View enrollment API keys", "List enrollment API keys."),
    ("member", "invite", "Invite members", "Add people to the organization as members."),
    ("member", "read", "View members", "List members and open their access details."),
    ("member", "remove", "Remove members", "Remove a member from the organization."),
    ("member", "update", "Update members", "Change a member's role or access scope."),
    ("mqtt", "test", "Use MQTT test tools", "Publish and listen in the organization MQTT test UI."),
    ("organization", "delete", "Delete organization", "Permanently delete the organization."),
    ("organization", "read", "View organization", "See the organization overview and non-ownership settings."),
    ("organization", "update", "Update organization", "Change the organization name, description, and settings."),
    ("team", "create", "Create teams", "Create new teams."),
    ("team", "delete", "Delete teams", "Delete teams. Members stay in the organization."),
    ("team", "read", "View teams", "List teams and see who is on them."),
    ("team", "update", "Update teams", "Rename teams and add or remove their members."),
)

# (key, name, description)
SYSTEM_ROLES: tuple[tuple[str, str, str], ...] = (
    ("owner", "Owner", "Full control of the organization, including ownership and membership."),
    ("admin", "Admin", "Full operational access and membership management, without deleting the organization."),
    ("operator", "Operator", "Monitor and operate devices and deployments within assigned scope."),
    (
        "developer",
        "Developer",
        "Manage device types, artifacts, and deployments with limited production device control.",
    ),
    ("viewer", "Viewer", "Read-only access to organization resources within assigned scope."),
)

SYSTEM_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "owner": (
        "artifact.create",
        "artifact.delete",
        "artifact.read",
        "artifact.update",
        "deployment.cancel",
        "deployment.create",
        "deployment.read",
        "deployment.rollback",
        "device.create",
        "device.delete",
        "device.logs",
        "device.read",
        "device.reboot",
        "device.shell",
        "device.update",
        "device_group.create",
        "device_group.delete",
        "device_group.read",
        "device_group.update",
        "device_type.create",
        "device_type.delete",
        "device_type.read",
        "device_type.update",
        "endpoint.create",
        "endpoint.delete",
        "endpoint.read",
        "endpoint.update",
        "enrollment_key.manage",
        "enrollment_key.read",
        "member.invite",
        "member.read",
        "member.remove",
        "member.update",
        "mqtt.test",
        "organization.delete",
        "organization.read",
        "organization.update",
        "team.create",
        "team.delete",
        "team.read",
        "team.update",
    ),
    "admin": (
        "artifact.create",
        "artifact.delete",
        "artifact.read",
        "artifact.update",
        "deployment.cancel",
        "deployment.create",
        "deployment.read",
        "deployment.rollback",
        "device.create",
        "device.delete",
        "device.logs",
        "device.read",
        "device.reboot",
        "device.shell",
        "device.update",
        "device_group.create",
        "device_group.delete",
        "device_group.read",
        "device_group.update",
        "device_type.create",
        "device_type.delete",
        "device_type.read",
        "device_type.update",
        "endpoint.create",
        "endpoint.delete",
        "endpoint.read",
        "endpoint.update",
        "enrollment_key.manage",
        "enrollment_key.read",
        "member.invite",
        "member.read",
        "member.remove",
        "member.update",
        "mqtt.test",
        "organization.read",
        "organization.update",
        "team.create",
        "team.delete",
        "team.read",
        "team.update",
    ),
    "operator": (
        "artifact.read",
        "deployment.cancel",
        "deployment.create",
        "deployment.read",
        "device.create",
        "device.logs",
        "device.read",
        "device.reboot",
        "device.update",
        "device_group.read",
        "device_type.read",
        "endpoint.create",
        "endpoint.delete",
        "endpoint.read",
        "endpoint.update",
        "mqtt.test",
        "organization.read",
        "team.read",
    ),
    "developer": (
        "artifact.create",
        "artifact.delete",
        "artifact.read",
        "artifact.update",
        "deployment.cancel",
        "deployment.create",
        "deployment.read",
        "device.logs",
        "device.read",
        "device_group.read",
        "device_type.create",
        "device_type.delete",
        "device_type.read",
        "device_type.update",
        "endpoint.create",
        "endpoint.delete",
        "endpoint.read",
        "endpoint.update",
        "mqtt.test",
        "organization.read",
        "team.read",
    ),
    "viewer": (
        "artifact.read",
        "deployment.read",
        "device.read",
        "device_group.read",
        "device_type.read",
        "endpoint.read",
        "organization.read",
    ),
}


def _seed_authorization_catalog() -> None:
    permission_ids = {f"{resource}.{action}": uuid.uuid4() for resource, action, _, _ in PERMISSIONS}
    role_ids = {key: uuid.uuid4() for key, _, _ in SYSTEM_ROLES}
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.UUID()),
        sa.column("resource", sa.String()),
        sa.column("action", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.Text()),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.UUID()),
        sa.column("organization_id", sa.UUID()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_system", sa.Boolean()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.UUID()),
        sa.column("permission_id", sa.UUID()),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": permission_ids[f"{resource}.{action}"],
                "resource": resource,
                "action": action,
                "label": label,
                "description": description,
            }
            for resource, action, label, description in PERMISSIONS
        ],
    )
    op.bulk_insert(
        roles,
        [
            {
                "id": role_ids[key],
                "organization_id": None,
                "key": key,
                "name": name,
                "description": description,
                "is_system": True,
            }
            for key, name, description in SYSTEM_ROLES
        ],
    )
    op.bulk_insert(
        role_permissions,
        [
            {"role_id": role_ids[key], "permission_id": permission_ids[name]}
            for key, names in SYSTEM_ROLE_PERMISSIONS.items()
            for name in names
        ],
    )


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="users_email_key"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_table(
        "organizations",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="organizations_slug_key"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=False)
    op.create_table(
        "audit_events",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"], unique=False)
    op.create_index(
        "ix_audit_events_organization_id_created_at", "audit_events", ["organization_id", "created_at"], unique=False
    )
    op.create_table(
        "device_groups",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_device_groups_org_name"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_device_groups_org_slug"),
    )
    op.create_index("ix_device_groups_organization_id", "device_groups", ["organization_id"], unique=False)
    op.create_table(
        "device_types",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("architecture", sa.String(length=64), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_device_types_org_name"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_device_types_org_slug"),
    )
    op.create_index("ix_device_types_organization_id", "device_types", ["organization_id"], unique=False)
    op.create_table(
        "enrollment_api_keys",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_enrollment_api_keys_key_hash"),
    )
    op.create_index("ix_enrollment_api_keys_organization_id", "enrollment_api_keys", ["organization_id"], unique=False)
    op.create_table(
        "organization_previous_slugs",
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("slug"),
    )
    op.create_index(
        "ix_organization_previous_slugs_organization_id",
        "organization_previous_slugs",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "roles",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "key", name="uq_roles_org_key", postgresql_nulls_not_distinct=True),
    )
    op.create_index("ix_roles_organization_id", "roles", ["organization_id"], unique=False)
    op.create_table(
        "teams",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),
    )
    op.create_index(op.f("ix_teams_organization_id"), "teams", ["organization_id"], unique=False)
    op.create_table(
        "artifacts",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "os_image",
                "firmware",
                "docker",
                "docker_compose",
                "systemd",
                "configuration",
                "other",
                name="artifact_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
    )
    op.create_index("ix_artifacts_org_device_type_id", "artifacts", ["organization_id", "device_type_id"], unique=False)
    op.create_index("ix_artifacts_org_type", "artifacts", ["organization_id", "type"], unique=False)
    op.create_index("ix_artifacts_organization_id", "artifacts", ["organization_id"], unique=False)
    op.create_index(
        "uq_artifacts_org_type_name_version",
        "artifacts",
        [
            "organization_id",
            "device_type_id",
            "type",
            sa.literal_column("lower(name)"),
            sa.literal_column("lower(version)"),
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "organization_memberships",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_organization_memberships_role_id_roles", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_membership_org_user"),
    )
    op.create_index(
        op.f("ix_organization_memberships_organization_id"),
        "organization_memberships",
        ["organization_id"],
        unique=False,
    )
    op.create_index(op.f("ix_organization_memberships_role_id"), "organization_memberships", ["role_id"], unique=False)
    op.create_index(op.f("ix_organization_memberships_user_id"), "organization_memberships", ["user_id"], unique=False)
    op.create_table(
        "registration_tokens",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("device_group_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_group_id"], ["device_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_registration_tokens_token_hash"),
    )
    op.create_index("ix_registration_tokens_organization_id", "registration_tokens", ["organization_id"], unique=False)
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_table(
        "access_bindings",
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["membership_id"], ["organization_memberships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "membership_id",
            "scope_type",
            "scope_id",
            name="uq_access_bindings_membership_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_access_bindings_membership_id", "access_bindings", ["membership_id"], unique=False)
    op.create_index("ix_access_bindings_scope", "access_bindings", ["scope_type", "scope_id"], unique=False)
    op.create_table(
        "devices",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("device_group_id", sa.UUID(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.Column("mac_addresses", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("os_name", sa.String(length=255), nullable=True),
        sa.Column("os_version", sa.String(length=255), nullable=True),
        sa.Column("kernel_version", sa.String(length=255), nullable=True),
        sa.Column("architecture", sa.String(length=64), nullable=True),
        sa.Column("cpu_model", sa.String(length=255), nullable=True),
        sa.Column("cpu_cores", sa.Integer(), nullable=True),
        sa.Column("memory_mb", sa.Integer(), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=True),
        sa.Column("credential_prefix", sa.String(length=32), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_token_id", sa.UUID(), nullable=True),
        sa.Column("mqtt_status", sa.String(length=16), nullable=True),
        sa.Column("mqtt_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mqtt_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mqtt_metrics_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_group_id"], ["device_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["registration_token_id"], ["registration_tokens.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_hash", name="uq_devices_credential_hash"),
    )
    op.create_index("ix_devices_org_device_group_id", "devices", ["organization_id", "device_group_id"], unique=False)
    op.create_index("ix_devices_org_device_type_id", "devices", ["organization_id", "device_type_id"], unique=False)
    op.create_index("ix_devices_org_last_seen_at", "devices", ["organization_id", "last_seen_at"], unique=False)
    op.create_index("ix_devices_org_serial_number", "devices", ["organization_id", "serial_number"], unique=False)
    op.create_index("ix_devices_organization_id", "devices", ["organization_id"], unique=False)
    op.create_table(
        "releases",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("systemd", "docker", "docker_compose", name="release_kind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("artifact_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_releases_artifact_id", "releases", ["artifact_id"], unique=False)
    op.create_index("ix_releases_org_kind", "releases", ["organization_id", "kind"], unique=False)
    op.create_index("ix_releases_organization_id", "releases", ["organization_id"], unique=False)
    op.create_index(
        "uq_releases_org_kind_name_version",
        "releases",
        ["organization_id", "kind", sa.literal_column("lower(name)"), sa.literal_column("lower(version)")],
        unique=True,
    )
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["membership_id"], ["organization_memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "membership_id"),
    )
    op.create_index(op.f("ix_team_members_membership_id"), "team_members", ["membership_id"], unique=False)
    op.create_table(
        "deployments",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("release_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "rollout",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='{"strategy": "immediate"}',
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="in_progress", nullable=False),
        sa.Column("provider", sa.String(length=32), server_default="native", nullable=False),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("rollback_of_deployment_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rollback_of_deployment_id"], ["deployments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployments_org_status", "deployments", ["organization_id", "status"], unique=False)
    op.create_index("ix_deployments_organization_id", "deployments", ["organization_id"], unique=False)
    op.create_index("ix_deployments_provider_ref", "deployments", ["provider", "provider_ref"], unique=False)
    op.create_index("ix_deployments_release_id", "deployments", ["release_id"], unique=False)
    op.create_table(
        "device_commands",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_commands_device_id", "device_commands", ["device_id"], unique=False)
    op.create_index("ix_device_commands_organization_id", "device_commands", ["organization_id"], unique=False)
    op.create_table(
        "device_connectivity",
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), server_default="wireguard", nullable=False),
        sa.Column("wg_public_key", sa.String(length=64), nullable=True),
        sa.Column("wg_address", postgresql.INET(), nullable=True),
        sa.Column("ssh_user", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("last_handshake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ssh_host_key", sa.String(length=2048), nullable=True),
        sa.Column("ssh_auth", sa.String(length=16), nullable=True),
        sa.Column("last_ssh_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ssh_error", sa.String(length=255), nullable=True),
        sa.Column("last_ssh_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
        sa.UniqueConstraint("wg_address", name="uq_device_connectivity_wg_address"),
        sa.UniqueConstraint("wg_public_key", name="uq_device_connectivity_wg_public_key"),
    )
    op.create_index("ix_device_connectivity_organization_id", "device_connectivity", ["organization_id"], unique=False)
    op.create_index("ix_device_connectivity_status", "device_connectivity", ["status"], unique=False)
    op.create_table(
        "device_enrollment_requests",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("api_key_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("claim_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("claim_secret_prefix", sa.String(length=32), nullable=False),
        sa.Column("requested_name", sa.String(length=255), nullable=True),
        sa.Column("assigned_name", sa.String(length=255), nullable=True),
        sa.Column("device_type_id", sa.UUID(), nullable=True),
        sa.Column("device_group_id", sa.UUID(), nullable=True),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.Column("mac_addresses", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("os_name", sa.String(length=255), nullable=True),
        sa.Column("os_version", sa.String(length=255), nullable=True),
        sa.Column("kernel_version", sa.String(length=255), nullable=True),
        sa.Column("architecture", sa.String(length=64), nullable=True),
        sa.Column("cpu_model", sa.String(length=255), nullable=True),
        sa.Column("cpu_cores", sa.Integer(), nullable=True),
        sa.Column("memory_mb", sa.Integer(), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["enrollment_api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_group_id"], ["device_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_type_id"], ["device_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_enrollment_requests_org_status",
        "device_enrollment_requests",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_device_enrollment_requests_organization_id", "device_enrollment_requests", ["organization_id"], unique=False
    )
    op.create_table(
        "device_mqtt_credentials",
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("password_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_table(
        "device_shell_sessions",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("ticket_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="shell", nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_hash", name="uq_device_shell_sessions_ticket_hash"),
    )
    op.create_index("ix_device_shell_sessions_device_id", "device_shell_sessions", ["device_id"], unique=False)
    op.create_index(
        "ix_device_shell_sessions_organization_id", "device_shell_sessions", ["organization_id"], unique=False
    )
    op.create_table(
        "device_software",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("workload_key", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("desired_release_id", sa.UUID(), nullable=True),
        sa.Column("desired_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("desired_spec", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("installed_release_id", sa.UUID(), nullable=True),
        sa.Column("installed_version", sa.String(length=64), nullable=True),
        sa.Column("health", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("sync_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("progress_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["desired_release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["installed_release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "workload_key", name="uq_device_software_device_workload"),
    )
    op.create_index("ix_device_software_desired_release_id", "device_software", ["desired_release_id"], unique=False)
    op.create_index("ix_device_software_device_id", "device_software", ["device_id"], unique=False)
    op.create_index("ix_device_software_organization_id", "device_software", ["organization_id"], unique=False)
    op.create_table(
        "application_endpoints",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=True),
        sa.Column("device_software_id", sa.UUID(), nullable=True),
        sa.Column("workload_key", sa.String(length=255), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("protocol", sa.String(length=16), server_default="http", nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("availability", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("port >= 1 AND port <= 65535", name="ck_application_endpoints_port"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_software_id"], ["device_software.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hostname", name="uq_application_endpoints_hostname"),
    )
    op.create_index("ix_application_endpoints_device_id", "application_endpoints", ["device_id"], unique=False)
    op.create_index(
        "ix_application_endpoints_organization_id", "application_endpoints", ["organization_id"], unique=False
    )
    op.create_index(
        "uq_application_endpoints_device_port_enabled",
        "application_endpoints",
        ["device_id", "port"],
        unique=True,
        postgresql_where=sa.text("enabled IS TRUE"),
    )
    op.create_table(
        "deployment_devices",
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("previous_desired_release_id", sa.UUID(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("desired_revision", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["previous_desired_release_id"], ["releases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id", "device_id", name="uq_deployment_devices_deployment_device"),
    )
    op.create_index("ix_deployment_devices_deployment_id", "deployment_devices", ["deployment_id"], unique=False)
    op.create_index(
        "ix_deployment_devices_deployment_status", "deployment_devices", ["deployment_id", "status"], unique=False
    )
    op.create_index("ix_deployment_devices_device_id", "deployment_devices", ["device_id"], unique=False)
    _seed_authorization_catalog()


def downgrade() -> None:
    op.drop_index("ix_deployment_devices_device_id", table_name="deployment_devices")
    op.drop_index("ix_deployment_devices_deployment_status", table_name="deployment_devices")
    op.drop_index("ix_deployment_devices_deployment_id", table_name="deployment_devices")
    op.drop_table("deployment_devices")
    op.drop_index(
        "uq_application_endpoints_device_port_enabled",
        table_name="application_endpoints",
        postgresql_where=sa.text("enabled IS TRUE"),
    )
    op.drop_index("ix_application_endpoints_organization_id", table_name="application_endpoints")
    op.drop_index("ix_application_endpoints_device_id", table_name="application_endpoints")
    op.drop_table("application_endpoints")
    op.drop_index("ix_device_software_organization_id", table_name="device_software")
    op.drop_index("ix_device_software_device_id", table_name="device_software")
    op.drop_index("ix_device_software_desired_release_id", table_name="device_software")
    op.drop_table("device_software")
    op.drop_index("ix_device_shell_sessions_organization_id", table_name="device_shell_sessions")
    op.drop_index("ix_device_shell_sessions_device_id", table_name="device_shell_sessions")
    op.drop_table("device_shell_sessions")
    op.drop_table("device_mqtt_credentials")
    op.drop_index("ix_device_enrollment_requests_organization_id", table_name="device_enrollment_requests")
    op.drop_index("ix_device_enrollment_requests_org_status", table_name="device_enrollment_requests")
    op.drop_table("device_enrollment_requests")
    op.drop_index("ix_device_connectivity_status", table_name="device_connectivity")
    op.drop_index("ix_device_connectivity_organization_id", table_name="device_connectivity")
    op.drop_table("device_connectivity")
    op.drop_index("ix_device_commands_organization_id", table_name="device_commands")
    op.drop_index("ix_device_commands_device_id", table_name="device_commands")
    op.drop_table("device_commands")
    op.drop_index("ix_deployments_release_id", table_name="deployments")
    op.drop_index("ix_deployments_provider_ref", table_name="deployments")
    op.drop_index("ix_deployments_organization_id", table_name="deployments")
    op.drop_index("ix_deployments_org_status", table_name="deployments")
    op.drop_table("deployments")
    op.drop_index(op.f("ix_team_members_membership_id"), table_name="team_members")
    op.drop_table("team_members")
    op.drop_index("uq_releases_org_kind_name_version", table_name="releases")
    op.drop_index("ix_releases_organization_id", table_name="releases")
    op.drop_index("ix_releases_org_kind", table_name="releases")
    op.drop_index("ix_releases_artifact_id", table_name="releases")
    op.drop_table("releases")
    op.drop_index("ix_devices_organization_id", table_name="devices")
    op.drop_index("ix_devices_org_serial_number", table_name="devices")
    op.drop_index("ix_devices_org_last_seen_at", table_name="devices")
    op.drop_index("ix_devices_org_device_type_id", table_name="devices")
    op.drop_index("ix_devices_org_device_group_id", table_name="devices")
    op.drop_table("devices")
    op.drop_index("ix_access_bindings_scope", table_name="access_bindings")
    op.drop_index("ix_access_bindings_membership_id", table_name="access_bindings")
    op.drop_table("access_bindings")
    op.drop_table("role_permissions")
    op.drop_index("ix_registration_tokens_organization_id", table_name="registration_tokens")
    op.drop_table("registration_tokens")
    op.drop_index(op.f("ix_organization_memberships_user_id"), table_name="organization_memberships")
    op.drop_index(op.f("ix_organization_memberships_role_id"), table_name="organization_memberships")
    op.drop_index(op.f("ix_organization_memberships_organization_id"), table_name="organization_memberships")
    op.drop_table("organization_memberships")
    op.drop_index("uq_artifacts_org_type_name_version", table_name="artifacts", postgresql_nulls_not_distinct=True)
    op.drop_index("ix_artifacts_organization_id", table_name="artifacts")
    op.drop_index("ix_artifacts_org_type", table_name="artifacts")
    op.drop_index("ix_artifacts_org_device_type_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index(op.f("ix_teams_organization_id"), table_name="teams")
    op.drop_table("teams")
    op.drop_index("ix_roles_organization_id", table_name="roles")
    op.drop_table("roles")
    op.drop_index("ix_organization_previous_slugs_organization_id", table_name="organization_previous_slugs")
    op.drop_table("organization_previous_slugs")
    op.drop_index("ix_enrollment_api_keys_organization_id", table_name="enrollment_api_keys")
    op.drop_table("enrollment_api_keys")
    op.drop_index("ix_device_types_organization_id", table_name="device_types")
    op.drop_table("device_types")
    op.drop_index("ix_device_groups_organization_id", table_name="device_groups")
    op.drop_table("device_groups")
    op.drop_index("ix_audit_events_organization_id_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("permissions")
