"""Organization membership policy helpers (role assignment rules).

Resource permissions are enforced by ``AuthzService``. These helpers only
answer "may this actor assign or modify that membership role?"
"""

from __future__ import annotations

from app.tenancy.models import OrganizationRole

ROLE_RANK = {
    OrganizationRole.VIEWER: 1,
    OrganizationRole.DEVELOPER: 2,
    OrganizationRole.OPERATOR: 3,
    OrganizationRole.ADMIN: 4,
    OrganizationRole.OWNER: 5,
}

_ADMIN_ASSIGNABLE = {
    OrganizationRole.OPERATOR,
    OrganizationRole.DEVELOPER,
    OrganizationRole.VIEWER,
}


def can_assign_role(actor_role: OrganizationRole, target_role: OrganizationRole) -> bool:
    """Return whether the actor may assign the target role."""
    if actor_role is OrganizationRole.OWNER:
        return True
    if actor_role is OrganizationRole.ADMIN:
        return target_role in _ADMIN_ASSIGNABLE
    return False


def can_modify_member(
    actor_role: OrganizationRole,
    target_role: OrganizationRole,
) -> bool:
    """Return whether the actor may change or remove a member with target_role."""
    if actor_role is OrganizationRole.OWNER:
        return True
    if actor_role is OrganizationRole.ADMIN:
        return target_role in _ADMIN_ASSIGNABLE
    return False
