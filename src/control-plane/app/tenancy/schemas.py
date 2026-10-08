"""Organization request and response schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.identity.schemas import normalize_email, validate_password_strength
from app.tenancy.models import OrganizationRole

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(value: str) -> str:
    """Suggest a URL slug from a display name."""
    lowered = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered)
    return cleaned.strip("-")


def validate_slug(value: str) -> str:
    slug = value.strip().lower()
    if not slug or not _SLUG_PATTERN.fullmatch(slug):
        raise ValueError("Slug must be lowercase and may only contain letters, numbers, and hyphens")
    if len(slug) > 100:
        raise ValueError("Slug must be at most 100 characters")
    return slug


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_slug(value)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class OrganizationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_slug(value)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    current_user_role: OrganizationRole
    member_count: int | None = None


class MemberScopeRequest(BaseModel):
    """Empty device_group_ids (or omitted) means entire organization."""

    device_group_ids: list[uuid.UUID] = Field(default_factory=list)


class MemberAddRequest(BaseModel):
    """Add a member by email.

    full_name and password are required only when no account exists for the
    email yet; they are ignored for existing users.
    """

    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = None
    role: OrganizationRole = OrganizationRole.OPERATOR
    scope: MemberScopeRequest | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return normalize_email(str(value))

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return validate_password_strength(value)


class MemberRoleUpdateRequest(BaseModel):
    role: OrganizationRole | None = None
    scope: MemberScopeRequest | None = None


class MemberScopeResponse(BaseModel):
    mode: str
    device_group_ids: list[uuid.UUID] = Field(default_factory=list)
    device_group_names: list[str] = Field(default_factory=list)


class TeamRef(BaseModel):
    id: uuid.UUID
    name: str


class MemberResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: OrganizationRole
    role_name: str
    status: str
    scope: MemberScopeResponse
    teams: list[TeamRef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


def _clean_team_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Team name must not be blank")
    return cleaned


def _clean_description(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    membership_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _clean_team_name(value)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return _clean_description(value)


class TeamUpdateRequest(BaseModel):
    """Omitted fields are unchanged; an explicit null description clears it."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_team_name(value)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        return _clean_description(value)


class TeamMemberAddRequest(BaseModel):
    membership_id: uuid.UUID


class TeamMemberResponse(BaseModel):
    membership_id: uuid.UUID
    email: str
    full_name: str
    role: OrganizationRole
    role_name: str


class TeamSummaryResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    member_count: int
    created_at: datetime
    updated_at: datetime


class TeamResponse(TeamSummaryResponse):
    members: list[TeamMemberResponse]
