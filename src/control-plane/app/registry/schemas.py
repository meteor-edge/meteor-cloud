"""Registry API schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

# One path component of an OCI repository name (distribution spec).
COMPONENT = r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*"
_PREFIX_RE = re.compile(rf"{COMPONENT}(?:/{COMPONENT})*")


class RegistryInfoResponse(BaseModel):
    enabled: bool
    # Host and namespace are null while the registry is off.
    host: str | None = None
    namespace: str | None = None


class RegistryTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["personal", "robot"] = "personal"
    access: Literal["pull", "push"] = "push"
    repository_prefix: str | None = Field(default=None, max_length=200)
    expires_at: AwareDatetime | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("repository_prefix")
    @classmethod
    def _valid_prefix(cls, value: str | None) -> str | None:
        value = (value or "").strip().removesuffix("/*").strip("/")
        if not value:
            return None
        if not _PREFIX_RE.fullmatch(value):
            raise ValueError("must be a lowercase repository path such as ci or team/apps")
        return value

    @model_validator(mode="after")
    def _prefix_is_for_robots(self) -> RegistryTokenCreateRequest:
        if self.repository_prefix and self.kind != "robot":
            raise ValueError("repository_prefix is only for robot tokens")
        return self


class RegistryTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    access: str
    token_prefix: str
    repository_prefix: str | None
    user_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class RegistryTokenCreateResponse(RegistryTokenResponse):
    token: str


class RegistryAccessTokenResponse(BaseModel):
    """Answer of the Docker token endpoint; clients read either ``token`` or ``access_token``."""

    token: str
    access_token: str
    expires_in: int
    issued_at: datetime
