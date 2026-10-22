"""Release request and response schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.releases.kinds import ReleaseKind

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class ReleaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=64)
    kind: ReleaseKind
    artifact_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    spec: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        cleaned = value.strip()
        if not _VERSION_PATTERN.fullmatch(cleaned):
            raise ValueError("Version may only contain letters, numbers, '.', '_', '+', and '-'")
        return cleaned

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ReleaseUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    spec: dict[str, Any] | None = None

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ReleaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    version: str
    kind: ReleaseKind
    artifact_id: uuid.UUID | None
    description: str | None
    spec: dict[str, Any]
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
