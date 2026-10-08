"""Effective access, catalog, and access-preview HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.catalog import PERMISSIONS, SYSTEM_ROLES
from app.authorization.schemas import (
    AccessPreviewRequest,
    AccessPreviewResponse,
    AccessPreviewStepResponse,
    AccessSummaryResponse,
    AuthzCatalogResponse,
    MeAccessResponse,
    MemberAccessResponse,
    OrganizationAccessResponse,
    PermissionCatalogItem,
    PermissionDetailResponse,
    RoleCatalogItem,
    RoleSummary,
    ScopeResponse,
)
from app.authorization.service import AuthzService, EffectiveAccess
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.devices.models import Device, DeviceGroup
from app.identity.dependencies import CurrentUser
from app.identity.repository import UserRepository
from app.tenancy.models import Organization, OrganizationMembership
from app.tenancy.repository import OrganizationRepository

router = APIRouter(tags=["authorization"])


def _group_names(session: Session, group_ids: tuple[uuid.UUID, ...]) -> list[str]:
    if not group_ids:
        return []
    rows = session.execute(select(DeviceGroup.id, DeviceGroup.name).where(DeviceGroup.id.in_(group_ids))).all()
    by_id = {row[0]: row[1] for row in rows}
    return [by_id[item] for item in group_ids if item in by_id]


def _to_org_access(
    session: Session,
    organization: Organization,
    membership: OrganizationMembership,
    access: EffectiveAccess,
    *,
    include_detail: bool,
) -> OrganizationAccessResponse:
    scope = ScopeResponse(
        mode=access.scope.mode,
        device_group_ids=list(access.scope.device_group_ids),
        device_group_names=_group_names(session, access.scope.device_group_ids),
    )
    detail = None
    if include_detail:
        detail = [
            PermissionDetailResponse(
                id=item.id,
                label=item.label,
                description=item.description,
                resource=item.resource,
                action=item.action,
                granted=item.granted,
                source=item.source,
                role_key=item.role_key,
                reason=item.reason,
                scope_mode=item.scope_mode,
            )
            for item in access.details
        ]
    return OrganizationAccessResponse(
        organization_id=organization.id,
        organization_name=organization.name,
        role=RoleSummary(key=access.role_key, name=access.role_name, is_system=access.is_system_role),
        status=access.status,
        scope=scope,
        permissions=sorted(access.permission_ids),
        summary=AccessSummaryResponse(
            granted_count=access.granted_count,
            denied_count=access.denied_count,
            by_resource=access.summary_by_resource,
        ),
        permissions_detail=detail,
    )


@router.get("/api/v1/authz/catalog", response_model=AuthzCatalogResponse)
def get_authz_catalog(_: CurrentUser) -> AuthzCatalogResponse:
    """Return system roles and the permission catalog for admin UIs."""
    return AuthzCatalogResponse(
        roles=[
            RoleCatalogItem(
                key=role.key,
                name=role.name,
                description=role.description,
                permissions=sorted(role.permissions),
            )
            for role in SYSTEM_ROLES
        ],
        permissions=[
            PermissionCatalogItem(
                id=item.id,
                resource=item.resource,
                action=item.action,
                label=item.label,
                description=item.description,
            )
            for item in PERMISSIONS
        ],
    )


@router.get("/api/v1/me/access", response_model=MeAccessResponse)
def get_my_access(current_user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> MeAccessResponse:
    """Return effective access for every organization the current user belongs to."""
    authz = AuthzService(db)
    rows = OrganizationRepository(db).list_for_user(current_user.id)
    organizations: list[OrganizationAccessResponse] = []
    for organization, membership in rows:
        if membership.status != "active":
            continue
        access = authz.effective_access(membership)
        organizations.append(
            _to_org_access(db, organization, membership, access, include_detail=False)
        )
    return MeAccessResponse(organizations=organizations)


@router.get(
    "/api/v1/organizations/{organization_id}/members/{membership_id}/access",
    response_model=MemberAccessResponse,
)
def get_member_access(
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> MemberAccessResponse:
    """Return a member's effective access for administrators."""
    authz = AuthzService(db)
    actor = authz.require_membership(user_id=current_user.id, organization_id=organization_id)
    authz.require(actor, "member.read")

    membership = db.get(OrganizationMembership, membership_id)
    if membership is None or membership.organization_id != organization_id:
        raise NotFoundError("member_not_found", "Organization member was not found.")

    organization = db.get(Organization, organization_id)
    assert organization is not None
    user = UserRepository(db).get_by_id(membership.user_id)
    assert user is not None
    access = authz.effective_access(membership)
    base = _to_org_access(db, organization, membership, access, include_detail=True)
    return MemberAccessResponse(
        **base.model_dump(),
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
    )


@router.post(
    "/api/v1/organizations/{organization_id}/access-preview",
    response_model=AccessPreviewResponse,
)
def preview_access(
    organization_id: uuid.UUID,
    payload: AccessPreviewRequest,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> AccessPreviewResponse:
    """Explain whether a member may perform a permission on an optional resource."""
    authz = AuthzService(db)
    actor = authz.require_membership(user_id=current_user.id, organization_id=organization_id)
    authz.require(actor, "member.read")

    membership = db.get(OrganizationMembership, payload.membership_id)
    if membership is None or membership.organization_id != organization_id:
        raise NotFoundError("member_not_found", "Organization member was not found.")

    device_group_id: uuid.UUID | None = None
    resource_label = None
    if payload.resource_type == "device" and payload.resource_id is not None:
        device = db.scalar(
            select(Device).where(Device.id == payload.resource_id, Device.organization_id == organization_id)
        )
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        device_group_id = device.device_group_id
        resource_label = f"Device {device.name}"
    elif payload.resource_type == "device_group" and payload.resource_id is not None:
        group = db.scalar(
            select(DeviceGroup).where(
                DeviceGroup.id == payload.resource_id,
                DeviceGroup.organization_id == organization_id,
            )
        )
        if group is None:
            raise NotFoundError("device_group_not_found", "Device group was not found.")
        device_group_id = group.id
        resource_label = f"Device group {group.name}"

    preview = authz.preview(
        membership,
        payload.permission,
        device_group_id=device_group_id,
        resource_label=resource_label,
    )
    return AccessPreviewResponse(
        allowed=preview.allowed,
        steps=[AccessPreviewStepResponse(ok=step.ok, detail=step.detail) for step in preview.steps],
    )
