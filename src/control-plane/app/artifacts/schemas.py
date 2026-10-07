"""Artifact request and response schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.artifacts.models import ArtifactType

# Versions are free-form (semver, dates, build ids) but must be URL/file safe.
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class ArtifactCreate(BaseModel):
    """Metadata submitted alongside an uploaded artifact file."""

    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=64)
    type: ArtifactType = ArtifactType.OS_IMAGE
    device_type_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Trim the name and raise ValueError if it is blank."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name must not be blank")
        return cleaned

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        """Trim a free-form version and reject unsafe or empty values with ValueError.

        The first character must be an ASCII letter or digit; later characters
        may also be periods, underscores, plus signs, or hyphens.
        """
        cleaned = value.strip()
        if not _VERSION_PATTERN.fullmatch(cleaned):
            raise ValueError("Version may only contain letters, numbers, '.', '_', '+', and '-'")
        return cleaned

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        """Trim the description, returning None for missing or blank text."""
        if value is None:
            return None
        return value.strip() or None


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_type_id: uuid.UUID | None
    name: str
    version: str
    type: ArtifactType
    description: str | None
    file_name: str
    content_type: str | None
    size_bytes: int
    checksum_sha256: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ArtifactDownloadLinkResponse(BaseModel):
    """A short-lived ticket that downloads the artifact without a bearer header.

    Browsers redeem it by submitting a form POST to ``url`` with a ``ticket`` field,
    so the ticket never appears in a URL, browser history, or proxy request logs.
    """

    url: str
    ticket: str
    expires_at: datetime
