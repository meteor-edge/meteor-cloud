"""Central authorization: check, require, effective access, preview."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.authorization.catalog import DEVICE_SCOPED_RESOURCES, PERMISSION_BY_ID, PERMISSIONS
from app.authorization.models import AccessBinding, MembershipStatus, Permission, Role, RolePermission, ScopeType
from app.core.exceptions import ForbiddenError, NotFoundError
from app.tenancy.models import OrganizationMembership


@dataclass(frozen=True, slots=True)
class ScopeView:
    mode: str  # organization | device_groups
    device_group_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class PermissionDetail:
    id: str
    label: str
    description: str
    resource: str
    action: str
    granted: bool
    source: str | None = None
    role_key: str | None = None
    reason: str | None = None
    scope_mode: str | None = None


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    organization_id: uuid.UUID
    role_key: str
    role_name: str
    is_system_role: bool
    status: str
    scope: ScopeView
    permission_ids: frozenset[str]
    details: tuple[PermissionDetail, ...]
    summary_by_resource: dict[str, list[str]] = field(default_factory=dict)

    @property
    def granted_count(self) -> int:
        return len(self.permission_ids)

    @property
    def denied_count(self) -> int:
        return len(PERMISSIONS) - self.granted_count


@dataclass(frozen=True, slots=True)
class PreviewStep:
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class AccessPreview:
    allowed: bool
    steps: tuple[PreviewStep, ...]


class AuthzService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active_membership(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> OrganizationMembership | None:
        statement = (
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.role_ref), selectinload(OrganizationMembership.bindings))
            .where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
        membership = self.session.scalar(statement)
        if membership is None or membership.status != MembershipStatus.ACTIVE.value:
            return None
        return membership

    def require_membership(self, *, user_id: uuid.UUID, organization_id: uuid.UUID) -> OrganizationMembership:
        membership = self.get_active_membership(user_id=user_id, organization_id=organization_id)
        if membership is None:
            raise NotFoundError("organization_not_found", "Organization was not found.")
        return membership

    def permission_ids_for_role(self, role_id: uuid.UUID) -> frozenset[str]:
        statement = (
            select(Permission.resource, Permission.action)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        return frozenset(f"{resource}.{action}" for resource, action in self.session.execute(statement).all())

    def scope_for(self, membership: OrganizationMembership) -> ScopeView:
        group_ids = tuple(
            sorted(
                (
                    binding.scope_id
                    for binding in membership.bindings
                    if binding.scope_type == ScopeType.DEVICE_GROUP.value and binding.scope_id is not None
                ),
                key=str,
            )
        )
        if group_ids:
            return ScopeView(mode="device_groups", device_group_ids=group_ids)
        return ScopeView(mode="organization")

    def has_permission(self, membership: OrganizationMembership, permission: str) -> bool:
        return permission in self.permission_ids_for_role(membership.role_id)

    def allows_device_group(
        self, membership: OrganizationMembership, device_group_id: uuid.UUID | None
    ) -> bool:
        """Device-group scope check. Null group is only visible to org-wide memberships."""
        scope = self.scope_for(membership)
        if scope.mode == "organization":
            return True
        if device_group_id is None:
            return False
        return device_group_id in scope.device_group_ids

    def check(
        self,
        membership: OrganizationMembership,
        permission: str,
        *,
        device_group_id: uuid.UUID | None = None,
    ) -> bool:
        if not self.has_permission(membership, permission):
            return False
        resource = permission.split(".", 1)[0]
        if resource not in DEVICE_SCOPED_RESOURCES:
            return True
        return self.allows_device_group(membership, device_group_id)

    def require(
        self,
        membership: OrganizationMembership,
        permission: str,
        *,
        device_group_id: uuid.UUID | None = None,
    ) -> None:
        if not self.check(membership, permission, device_group_id=device_group_id):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to perform this action.",
            )

    def effective_access(self, membership: OrganizationMembership) -> EffectiveAccess:
        role = membership.role_ref
        granted = self.permission_ids_for_role(membership.role_id)
        scope = self.scope_for(membership)
        details: list[PermissionDetail] = []
        summary: dict[str, list[str]] = {}
        for item in PERMISSIONS:
            if item.id in granted:
                details.append(
                    PermissionDetail(
                        id=item.id,
                        label=item.label,
                        description=item.description,
                        resource=item.resource,
                        action=item.action,
                        granted=True,
                        source="role",
                        role_key=role.key,
                        scope_mode=scope.mode if item.resource in DEVICE_SCOPED_RESOURCES else "organization",
                    )
                )
                summary.setdefault(item.resource, []).append(item.action)
            else:
                details.append(
                    PermissionDetail(
                        id=item.id,
                        label=item.label,
                        description=item.description,
                        resource=item.resource,
                        action=item.action,
                        granted=False,
                        reason="not_in_role",
                    )
                )
        return EffectiveAccess(
            organization_id=membership.organization_id,
            role_key=role.key,
            role_name=role.name,
            is_system_role=role.is_system,
            status=membership.status,
            scope=scope,
            permission_ids=granted,
            details=tuple(details),
            summary_by_resource=summary,
        )

    def preview(
        self,
        membership: OrganizationMembership,
        permission: str,
        *,
        device_group_id: uuid.UUID | None = None,
        resource_label: str | None = None,
    ) -> AccessPreview:
        steps: list[PreviewStep] = []
        role = membership.role_ref
        steps.append(
            PreviewStep(True, f"Active member with role {role.name} ({role.key}).")
        )
        if permission not in PERMISSION_BY_ID:
            steps.append(PreviewStep(False, f"Unknown permission {permission}."))
            return AccessPreview(False, tuple(steps))

        if self.has_permission(membership, permission):
            steps.append(PreviewStep(True, f"Role {role.name} includes {permission}."))
        else:
            steps.append(PreviewStep(False, f"Role {role.name} does not include {permission}."))
            return AccessPreview(False, tuple(steps))

        resource = permission.split(".", 1)[0]
        if resource not in DEVICE_SCOPED_RESOURCES:
            steps.append(PreviewStep(True, f"{permission} is organization-wide (not device-group scoped)."))
            return AccessPreview(True, tuple(steps))

        scope = self.scope_for(membership)
        label = resource_label or "resource"
        if scope.mode == "organization":
            steps.append(PreviewStep(True, f"Membership is organization-wide; {label} is in scope."))
            return AccessPreview(True, tuple(steps))

        if device_group_id is None:
            steps.append(
                PreviewStep(
                    False,
                    f"{label} has no device group; scoped memberships cannot access ungrouped devices.",
                )
            )
            return AccessPreview(False, tuple(steps))

        if device_group_id in scope.device_group_ids:
            steps.append(PreviewStep(True, f"{label} is in an allowed device group."))
            return AccessPreview(True, tuple(steps))

        steps.append(PreviewStep(False, f"{label} is outside the membership device-group scope."))
        return AccessPreview(False, tuple(steps))

    def get_system_role(self, key: str) -> Role | None:
        return self.session.scalar(select(Role).where(Role.organization_id.is_(None), Role.key == key))

    def list_bindings(self, membership_id: uuid.UUID) -> list[AccessBinding]:
        return list(
            self.session.scalars(
                select(AccessBinding).where(AccessBinding.membership_id == membership_id)
            ).all()
        )

    def replace_device_group_scope(
        self, membership: OrganizationMembership, device_group_ids: list[uuid.UUID] | None
    ) -> None:
        """Set scope. None or empty list means organization-wide."""
        for binding in list(membership.bindings):
            self.session.delete(binding)
        self.session.flush()
        if not device_group_ids:
            return
        for group_id in dict.fromkeys(device_group_ids):
            self.session.add(
                AccessBinding(
                    membership_id=membership.id,
                    scope_type=ScopeType.DEVICE_GROUP.value,
                    scope_id=group_id,
                )
            )
        self.session.flush()
