"""Permission catalog and system role templates.

Permissions and system roles are defined in code and seeded into PostgreSQL.
Organizations may later add custom roles that reference the same permissions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionDef:
    resource: str
    action: str
    label: str
    description: str

    @property
    def id(self) -> str:
        return f"{self.resource}.{self.action}"


@dataclass(frozen=True, slots=True)
class SystemRoleDef:
    key: str
    name: str
    description: str
    permissions: frozenset[str]


PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef(
        "organization", "read", "View organization", "See the organization overview and non-ownership settings."
    ),
    PermissionDef(
        "organization", "update", "Update organization", "Change the organization name, description, and settings."
    ),
    PermissionDef("organization", "delete", "Delete organization", "Permanently delete the organization."),
    PermissionDef("member", "read", "View members", "List members and open their access details."),
    PermissionDef("member", "invite", "Invite members", "Add people to the organization as members."),
    PermissionDef("member", "update", "Update members", "Change a member's role or access scope."),
    PermissionDef("member", "remove", "Remove members", "Remove a member from the organization."),
    PermissionDef("team", "read", "View teams", "List teams and see who is on them."),
    PermissionDef("team", "create", "Create teams", "Create new teams."),
    PermissionDef("team", "update", "Update teams", "Rename teams and add or remove their members."),
    PermissionDef("team", "delete", "Delete teams", "Delete teams. Members stay in the organization."),
    PermissionDef("device_group", "read", "View device groups", "List and open device groups within scope."),
    PermissionDef("device_group", "create", "Create device groups", "Create new device groups."),
    PermissionDef("device_group", "update", "Update device groups", "Edit device groups within scope."),
    PermissionDef("device_group", "delete", "Delete device groups", "Delete device groups within scope."),
    PermissionDef("device", "read", "View devices", "List and open devices within scope."),
    PermissionDef("device", "create", "Create devices", "Create registration tokens and approve enrollment."),
    PermissionDef("device", "update", "Update devices", "Edit device metadata, type, group, and enablement."),
    PermissionDef("device", "delete", "Delete devices", "Remove devices within scope."),
    PermissionDef(
        "device",
        "reboot",
        "Restart and shut down devices",
        "Restart or shut down a device from the console.",
    ),
    PermissionDef("device_type", "read", "View device types", "List and open device types."),
    PermissionDef("device_type", "create", "Create device types", "Create hardware models."),
    PermissionDef("device_type", "update", "Update device types", "Edit device types."),
    PermissionDef("device_type", "delete", "Delete device types", "Delete device types."),
    PermissionDef("artifact", "read", "View artifacts", "List and download artifacts."),
    PermissionDef("artifact", "create", "Upload artifacts", "Upload new artifact versions."),
    PermissionDef("artifact", "update", "Update artifacts", "Edit artifact metadata."),
    PermissionDef("artifact", "delete", "Delete artifacts", "Delete artifacts and their stored objects."),
    PermissionDef(
        "artifact",
        "publish",
        "Publish OS images",
        "Show OS images in Meteor Imager, where anyone who can reach the server can download them.",
    ),
    PermissionDef("deployment", "read", "View deployments", "List and open deployments."),
    PermissionDef("deployment", "create", "Create deployments", "Start deployments."),
    PermissionDef("deployment", "cancel", "Cancel deployments", "Cancel in-progress deployments."),
    PermissionDef("deployment", "rollback", "Rollback deployments", "Roll back a deployment."),
    PermissionDef("endpoint", "read", "View endpoints", "List and open public application endpoints."),
    PermissionDef("endpoint", "create", "Create endpoints", "Register public application hostnames."),
    PermissionDef("endpoint", "update", "Update endpoints", "Change or disable public application endpoints."),
    PermissionDef("endpoint", "delete", "Delete endpoints", "Remove public application endpoints."),
    PermissionDef("device", "shell", "Open device terminal", "Open a browser SSH session to a device."),
    PermissionDef("device", "logs", "View device logs", "Read service, container and system logs on a device."),
    PermissionDef(
        "device",
        "network",
        "Device Wi-Fi and connection",
        "See which networks a device uses and add, change or remove its Wi-Fi. A wrong network can take it offline.",
    ),
    PermissionDef(
        "device",
        "users",
        "Manage device SSH users",
        "Change a user's password or add an SSH user on a device. As sensitive as a shell session.",
    ),
    PermissionDef("enrollment_key", "read", "View enrollment API keys", "List enrollment API keys."),
    PermissionDef("enrollment_key", "manage", "Manage enrollment API keys", "Create and revoke enrollment API keys."),
    PermissionDef("application", "read", "View applications", "List applications and their credentials."),
    PermissionDef("application", "create", "Create applications", "Register applications for the data plane."),
    PermissionDef("application", "update", "Update applications", "Change application details and stream permissions."),
    PermissionDef("application", "delete", "Delete applications", "Delete applications and their credentials."),
    PermissionDef(
        "application",
        "credential",
        "Manage application credentials",
        "Create, rotate and revoke data-plane credentials for applications.",
    ),
    PermissionDef("application", "command", "Send application commands", "Send commands to running applications."),
    PermissionDef("mqtt", "test", "Use MQTT test tools", "Publish and listen in the organization MQTT test UI."),
)

PERMISSION_BY_ID: dict[str, PermissionDef] = {item.id: item for item in PERMISSIONS}

# Resources whose instances are limited by device-group scope.
DEVICE_SCOPED_RESOURCES = frozenset({"device", "device_group", "endpoint"})

_ALL = frozenset(PERMISSION_BY_ID)
_VIEWER = frozenset(
    {
        "organization.read",
        "device_group.read",
        "device.read",
        "device_type.read",
        "artifact.read",
        "deployment.read",
        "endpoint.read",
        "application.read",
    }
)
_DEVELOPER = _VIEWER | frozenset(
    {
        "application.create",
        "application.update",
        "application.delete",
        "application.credential",
        "application.command",
        "team.read",
        "device_type.create",
        "device_type.update",
        "device_type.delete",
        "artifact.create",
        "artifact.update",
        "artifact.delete",
        "deployment.create",
        "deployment.cancel",
        "endpoint.create",
        "endpoint.update",
        "endpoint.delete",
        "mqtt.test",
        "device.logs",
    }
)
_OPERATOR = _VIEWER | frozenset(
    {
        "team.read",
        "device.create",
        "device.update",
        "application.command",
        # device.reboot / device.shell / device.users are owner/admin only.
        "device.logs",
        "deployment.create",
        "deployment.cancel",
        "endpoint.create",
        "endpoint.update",
        "endpoint.delete",
        "mqtt.test",
    }
)
_ADMIN = _ALL - frozenset({"organization.delete"})
_OWNER = _ALL

SYSTEM_ROLES: tuple[SystemRoleDef, ...] = (
    SystemRoleDef(
        key="owner",
        name="Owner",
        description="Full control of the organization, including ownership and membership.",
        permissions=_OWNER,
    ),
    SystemRoleDef(
        key="admin",
        name="Admin",
        description="Full operational access and membership management, without deleting the organization.",
        permissions=_ADMIN,
    ),
    SystemRoleDef(
        key="operator",
        name="Operator",
        description="Monitor and operate devices and deployments within assigned scope.",
        permissions=_OPERATOR,
    ),
    SystemRoleDef(
        key="developer",
        name="Developer",
        description="Manage device types, artifacts, and deployments with limited production device control.",
        permissions=_DEVELOPER,
    ),
    SystemRoleDef(
        key="viewer",
        name="Viewer",
        description="Read-only access to organization resources within assigned scope.",
        permissions=_VIEWER,
    ),
)

SYSTEM_ROLE_BY_KEY: dict[str, SystemRoleDef] = {role.key: role for role in SYSTEM_ROLES}

# Legacy enum value → system role key
LEGACY_ROLE_MAP = {
    "owner": "owner",
    "admin": "admin",
    "member": "operator",
    "viewer": "viewer",
}
