"""Application and credential request/response schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.applications.streams import StreamPatternError, validate_stream_pattern

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
ENVIRONMENT_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAX_PATTERNS = 64


def _patterns(values: list[str], *, allow_reserved: bool) -> list[str]:
    if len(values) > MAX_PATTERNS:
        raise ValueError(f"at most {MAX_PATTERNS} stream patterns")
    out: list[str] = []
    for value in values:
        try:
            cleaned = validate_stream_pattern(value, allow_reserved=allow_reserved)
        except StreamPatternError as exc:
            raise ValueError(str(exc)) from exc
        if cleaned not in out:
            out.append(cleaned)
    return out


class ApplicationPermissions(BaseModel):
    """Least privilege: an application gets nothing it is not listed for."""

    model_config = ConfigDict(extra="forbid")

    publish: list[str] = Field(default_factory=list, description="Streams this app may publish telemetry/events to.")
    subscribe: list[str] = Field(default_factory=list, description="Streams this app may receive live.")
    read: list[str] = Field(default_factory=list, description="Streams this app may query history for.")
    receive_commands: bool = False
    send_commands: list[str] = Field(
        default_factory=list, description="Application slugs this app may send commands to."
    )

    @field_validator("publish")
    @classmethod
    def _check_publish(cls, value: list[str]) -> list[str]:
        return _patterns(value, allow_reserved=False)

    @field_validator("subscribe", "read")
    @classmethod
    def _check_patterns(cls, value: list[str]) -> list[str]:
        # Platform streams such as meteor.lifecycle can be received, never published.
        return _patterns(value, allow_reserved=True)

    @field_validator("send_commands")
    @classmethod
    def _check_targets(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_PATTERNS:
            raise ValueError(f"at most {MAX_PATTERNS} command targets")
        for slug in value:
            if not SLUG_RE.match(slug):
                raise ValueError(f"invalid application slug {slug!r}")
        return sorted(set(value))


class ApplicationCreate(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    permissions: ApplicationPermissions = Field(default_factory=ApplicationPermissions)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not SLUG_RE.match(value):
            raise ValueError("slug: 2-63 characters, a-z, 0-9 and '-', starting with a letter")
        return value


class ApplicationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    permissions: ApplicationPermissions | None = None


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    enabled: bool
    permissions: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    device_id: uuid.UUID | None = None
    environment: str = "production"
    expires_in_days: int = Field(default=90, ge=1, le=365)

    @field_validator("environment")
    @classmethod
    def _check_env(cls, value: str) -> str:
        if not ENVIRONMENT_RE.match(value):
            raise ValueError("environment: 1-32 characters, a-z, 0-9 and '-'")
        return value


class CredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    device_id: uuid.UUID | None
    environment: str
    name: str
    token_prefix: str
    expires_at: datetime
    revoked_at: datetime | None
    workload_key: str | None
    created_at: datetime


class CredentialSecretResponse(CredentialResponse):
    """Returned once, when a credential is created or rotated."""

    token: str
    endpoint: str | None


class CommandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    device_id: uuid.UUID | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=86_400)
    expires_in_seconds: int = Field(default=3600, ge=1, le=604_800)
    # 0: redeliver until the command expires.
    max_attempts: int = Field(default=0, ge=0, le=1000)
    idempotency_key: str | None = Field(default=None, max_length=128)


class AgentAppCredentialRequest(BaseModel):
    workload_key: str = Field(min_length=1, max_length=255)


class AgentAppCredentialResponse(BaseModel):
    """What the agent writes to the token file and the container environment."""

    token: str
    credential_id: uuid.UUID
    expires_at: datetime
    endpoint: str | None
    application: str
    application_id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    environment: str
    refresh_after: datetime


class PolicyCredential(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    application_id: uuid.UUID
    device_id: uuid.UUID | None
    environment: str
    token_hash: str
    expires_at: datetime


class PolicyApplication(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    slug: str
    permissions: dict[str, Any]


class PolicySnapshot(BaseModel):
    version: str
    generated_at: datetime
    applications: list[PolicyApplication]
    credentials: list[PolicyCredential]
