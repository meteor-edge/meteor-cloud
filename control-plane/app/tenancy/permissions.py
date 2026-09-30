"""Explicit organization permission checks."""

from __future__ import annotations

from app.core.exceptions import ForbiddenError
from app.tenancy.models import OrganizationRole

ROLE_RANK = {
    OrganizationRole.VIEWER: 1,
    OrganizationRole.MEMBER: 2,
    OrganizationRole.ADMIN: 3,
    OrganizationRole.OWNER: 4,
}


def require_role_at_least(role: OrganizationRole, minimum: OrganizationRole) -> None:
    if ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise ForbiddenError(
            "insufficient_permission",
            "You do not have permission to perform this action.",
        )


def can_view_organization(role: OrganizationRole) -> bool:
    return role in {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.MEMBER,
        OrganizationRole.VIEWER,
    }


def can_update_organization(role: OrganizationRole) -> bool:
    return role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}


def can_delete_organization(role: OrganizationRole) -> bool:
    return role is OrganizationRole.OWNER


def can_manage_members(role: OrganizationRole) -> bool:
    return role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}


def can_assign_role(actor_role: OrganizationRole, target_role: OrganizationRole) -> bool:
    """Return whether the actor may assign the target role."""
    if actor_role is OrganizationRole.OWNER:
        return True
    if actor_role is OrganizationRole.ADMIN:
        return target_role in {OrganizationRole.MEMBER, OrganizationRole.VIEWER}
    return False


def can_modify_member(
    actor_role: OrganizationRole,
    target_role: OrganizationRole,
) -> bool:
    """Return whether the actor may change or remove a member with target_role."""
    if actor_role is OrganizationRole.OWNER:
        return True
    if actor_role is OrganizationRole.ADMIN:
        return target_role in {OrganizationRole.MEMBER, OrganizationRole.VIEWER}
    return False
