"""Authorization API schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ScopeResponse(BaseModel):
    mode: str
    device_group_ids: list[uuid.UUID] = Field(default_factory=list)
    device_group_names: list[str] = Field(default_factory=list)


class RoleSummary(BaseModel):
    key: str
    name: str
    is_system: bool


class PermissionDetailResponse(BaseModel):
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


class AccessSummaryResponse(BaseModel):
    granted_count: int
    denied_count: int
    by_resource: dict[str, list[str]]


class OrganizationAccessResponse(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: RoleSummary
    status: str
    scope: ScopeResponse
    permissions: list[str]
    summary: AccessSummaryResponse
    permissions_detail: list[PermissionDetailResponse] | None = None


class MeAccessResponse(BaseModel):
    organizations: list[OrganizationAccessResponse]


class MemberAccessResponse(OrganizationAccessResponse):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str


class AccessPreviewRequest(BaseModel):
    membership_id: uuid.UUID
    permission: str = Field(min_length=3, max_length=129)
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None


class AccessPreviewStepResponse(BaseModel):
    ok: bool
    detail: str


class AccessPreviewResponse(BaseModel):
    allowed: bool
    steps: list[AccessPreviewStepResponse]


class RoleCatalogItem(BaseModel):
    key: str
    name: str
    description: str
    permissions: list[str]


class PermissionCatalogItem(BaseModel):
    id: str
    resource: str
    action: str
    label: str
    description: str


class AuthzCatalogResponse(BaseModel):
    roles: list[RoleCatalogItem]
    permissions: list[PermissionCatalogItem]
