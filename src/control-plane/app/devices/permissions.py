"""Explicit fleet permission checks.

Owners and admins may mutate fleet resources; members and viewers are
read-only. These mirror the organization RBAC conventions.
"""

from __future__ import annotations

from app.tenancy.models import OrganizationRole

_MANAGER_ROLES = {OrganizationRole.OWNER, OrganizationRole.ADMIN}
_VIEW_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.MEMBER,
    OrganizationRole.VIEWER,
}


def can_view_fleet(role: OrganizationRole) -> bool:
    return role in _VIEW_ROLES


def can_manage_fleet(role: OrganizationRole) -> bool:
    """Manage device types, groups, tokens, and device lifecycle actions."""
    return role in _MANAGER_ROLES
