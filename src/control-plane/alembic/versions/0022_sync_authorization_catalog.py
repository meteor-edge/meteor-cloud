"""Bring the permission catalog and system roles to their state as of this revision.

The catalog is only written by migrations, so databases created before device.shell,
endpoint.*, enrollment_key.* and mqtt.test existed never received them, and every role
was denied. This also takes device.shell away from operators (owner/admin only).

The catalog is frozen here rather than read from app.authorization: a migration must
do the same thing whenever it runs, whatever the application code says later.

Revision ID: 0022_sync_authorization_catalog
Revises: 0021_connectivity_ssh_auth
Create Date: 2026-10-24 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_sync_authorization_catalog"
down_revision: str | Sequence[str] | None = "0021_connectivity_ssh_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (resource, action, label, description)
PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
    ('organization', 'read', 'View organization', 'See the organization overview and non-ownership settings.'),
    ('organization', 'update', 'Update organization', 'Change the organization name, description, and settings.'),
    ('organization', 'delete', 'Delete organization', 'Permanently delete the organization.'),
    ('member', 'read', 'View members', 'List members and open their access details.'),
    ('member', 'invite', 'Invite members', 'Add people to the organization as members.'),
    ('member', 'update', 'Update members', "Change a member's role or access scope."),
    ('member', 'remove', 'Remove members', 'Remove a member from the organization.'),
    ('team', 'read', 'View teams', 'List teams and see who is on them.'),
    ('team', 'create', 'Create teams', 'Create new teams.'),
    ('team', 'update', 'Update teams', 'Rename teams and add or remove their members.'),
    ('team', 'delete', 'Delete teams', 'Delete teams. Members stay in the organization.'),
    ('device_group', 'read', 'View device groups', 'List and open device groups within scope.'),
    ('device_group', 'create', 'Create device groups', 'Create new device groups.'),
    ('device_group', 'update', 'Update device groups', 'Edit device groups within scope.'),
    ('device_group', 'delete', 'Delete device groups', 'Delete device groups within scope.'),
    ('device', 'read', 'View devices', 'List and open devices within scope.'),
    ('device', 'create', 'Create devices', 'Create registration tokens and approve enrollment.'),
    ('device', 'update', 'Update devices', 'Edit device metadata, type, group, and enablement.'),
    ('device', 'delete', 'Delete devices', 'Remove devices within scope.'),
    ('device', 'reboot', 'Reboot devices', 'Send reboot and similar operate commands within scope.'),
    ('device_type', 'read', 'View device types', 'List and open device types.'),
    ('device_type', 'create', 'Create device types', 'Create hardware models.'),
    ('device_type', 'update', 'Update device types', 'Edit device types.'),
    ('device_type', 'delete', 'Delete device types', 'Delete device types.'),
    ('artifact', 'read', 'View artifacts', 'List and download artifacts.'),
    ('artifact', 'create', 'Upload artifacts', 'Upload new artifact versions.'),
    ('artifact', 'update', 'Update artifacts', 'Edit artifact metadata.'),
    ('artifact', 'delete', 'Delete artifacts', 'Delete artifacts and their stored objects.'),
    ('deployment', 'read', 'View deployments', 'List and open deployments.'),
    ('deployment', 'create', 'Create deployments', 'Start deployments.'),
    ('deployment', 'cancel', 'Cancel deployments', 'Cancel in-progress deployments.'),
    ('deployment', 'rollback', 'Rollback deployments', 'Roll back a deployment.'),
    ('endpoint', 'read', 'View endpoints', 'List and open public application endpoints.'),
    ('endpoint', 'create', 'Create endpoints', 'Register public application hostnames.'),
    ('endpoint', 'update', 'Update endpoints', 'Change or disable public application endpoints.'),
    ('endpoint', 'delete', 'Delete endpoints', 'Remove public application endpoints.'),
    ('device', 'shell', 'Open device terminal', 'Open a browser SSH session to a device.'),
    ('enrollment_key', 'read', 'View enrollment API keys', 'List enrollment API keys.'),
    ('enrollment_key', 'manage', 'Manage enrollment API keys', 'Create and revoke enrollment API keys.'),
    ('mqtt', 'test', 'Use MQTT test tools', 'Publish and listen in the organization MQTT test UI.'),
)
ALL = frozenset(f"{resource}.{action}" for resource, action, _, _ in PERMISSIONS)
SYSTEM_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL,
    "admin": ALL - {"organization.delete"},
    "operator": frozenset(
        {
            "artifact.read",
            "deployment.cancel",
            "deployment.create",
            "deployment.read",
            "device.create",
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
        }
    ),
    "developer": frozenset(
        {
            "artifact.create",
            "artifact.delete",
            "artifact.read",
            "artifact.update",
            "deployment.cancel",
            "deployment.create",
            "deployment.read",
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
        }
    ),
    "viewer": frozenset(
        {
            "artifact.read",
            "deployment.read",
            "device.read",
            "device_group.read",
            "device_type.read",
            "endpoint.read",
            "organization.read",
        }
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    for resource, action, label, description in PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, label, description)"
                " VALUES (gen_random_uuid(), :resource, :action, :label, :description)"
                " ON CONFLICT (resource, action)"
                " DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description"
            ),
            {"resource": resource, "action": action, "label": label, "description": description},
        )
    permission_ids = {
        f"{row.resource}.{row.action}": row.id
        for row in bind.execute(sa.text("SELECT id, resource, action FROM permissions"))
    }
    for key, wanted in SYSTEM_ROLE_PERMISSIONS.items():
        role_id = bind.scalar(
            sa.text("SELECT id FROM roles WHERE organization_id IS NULL AND key = :key"),
            {"key": key},
        )
        if role_id is None:
            continue
        wanted_ids = [permission_ids[permission] for permission in sorted(wanted)]
        bind.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :role_id AND NOT (permission_id = ANY(:ids))"),
            {"role_id": role_id, "ids": wanted_ids},
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id)"
                " SELECT :role_id, unnest(CAST(:ids AS uuid[]))"
                " ON CONFLICT DO NOTHING"
            ),
            {"role_id": role_id, "ids": wanted_ids},
        )


def downgrade() -> None:
    # Earlier revisions do not reference these rows; leaving them is harmless.
    pass
