"""Legacy fleet helpers kept for callers that still pass a role key.

New code should call ``AuthzService.require`` with permission ids.
"""

from __future__ import annotations

from app.tenancy.models import OrganizationRole

_MANAGER_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.OPERATOR,
}
_VIEW_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.OPERATOR,
    OrganizationRole.DEVELOPER,
    OrganizationRole.VIEWER,
}


def can_view_fleet(role: OrganizationRole) -> bool:
    return role in _VIEW_ROLES


def can_manage_fleet(role: OrganizationRole) -> bool:
    """True for roles that historically could mutate fleet resources.

    Prefer permission checks: device.update, device_type.create, etc.
    """
    return role in _MANAGER_ROLES
