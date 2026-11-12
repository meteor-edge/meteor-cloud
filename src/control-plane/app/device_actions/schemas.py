"""Device action requests and responses. No response ever carries a password."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Debian's default NAME_REGEX for useradd, so the device accepts what the console does.
_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# Accounts the console must never touch, whatever their uid on a given image.
RESERVED_USERNAMES = frozenset({"root", "daemon", "bin", "sys", "sync", "nobody", "sshd", "systemd-network"})
_KEY = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|"
    r"sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]+={0,3}( [^\r\n]*)?$"
)


def _username(value: str) -> str:
    value = value.strip()
    if not _USERNAME.match(value):
        raise ValueError("The username must start with a lowercase letter or _ and use a-z, 0-9, _ or - (32 at most)")
    if value in RESERVED_USERNAMES:
        raise ValueError(f"The account {value!r} cannot be changed from the console")
    return value


def _password(value: str) -> str:
    if not 8 <= len(value) <= 128:
        raise ValueError("The password must be 8 to 128 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("The password must not contain line breaks or control characters")
    return value


class SetPasswordRequest(BaseModel):
    """Change the password of an existing user on the device."""

    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        return _username(value)

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        return _password(value)


class AddUserRequest(BaseModel):
    """Add a user who can log in over SSH."""

    username: str
    password: str
    sudo: bool = False
    authorized_key: str | None = Field(default=None, description="One SSH public key line (optional)")

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        return _username(value)

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        return _password(value)

    @field_validator("authorized_key")
    @classmethod
    def _valid_key(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if len(value) > 4096 or not _KEY.match(value):
            raise ValueError("The SSH public key must be one line such as 'ssh-ed25519 AAAA… you@laptop'")
        return value


class DeviceActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    state: str
    username: str | None
    sudo: bool
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
    reported_at: datetime | None


class DeviceActionsResponse(BaseModel):
    # Only the kinds the caller may order: power actions with device.reboot, user changes with device.users.
    actions: list[DeviceActionResponse]


# ------------------------------------------------------------------ agent side
class AgentDeviceAction(BaseModel):
    id: uuid.UUID
    kind: Literal["reboot", "shutdown", "set_password", "add_user"]
    username: str | None = None
    password: str | None = None
    sudo: bool = False
    authorized_key: str | None = None


class AgentDeviceActionsResponse(BaseModel):
    actions: list[AgentDeviceAction]


class AgentActionResult(BaseModel):
    id: uuid.UUID
    state: Literal["done", "failed"]
    error: str | None = Field(default=None, max_length=500)


class AgentActionReportRequest(BaseModel):
    results: list[AgentActionResult] = Field(default_factory=list, max_length=50)


class AgentActionReportResponse(BaseModel):
    accepted: int
