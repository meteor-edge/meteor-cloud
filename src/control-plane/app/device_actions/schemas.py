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


# The agent's module names (internal/modules in the Go agent): lowercase words.
_MODULE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
# Longest doctor report the server keeps (the agent cuts it to this).
MAX_ACTION_OUTPUT = 16000


class RestartModuleRequest(BaseModel):
    """Restart one agent module; a module the crash-loop breaker parked runs again."""

    module: str

    @field_validator("module")
    @classmethod
    def _valid_module(cls, value: str) -> str:
        value = value.strip()
        if not _MODULE.match(value):
            raise ValueError("The module name must be a lowercase word such as 'deploy'")
        return value


# Compose service names.
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SoftwareActionRequest(BaseModel):
    """Restart, stop or start a deployed workload, or ask the device to look at it again."""

    kind: Literal["workload_restart", "workload_stop", "workload_start", "workload_snapshot"]
    service: str | None = Field(default=None, description="One service of a compose workload; all of it when empty")

    @field_validator("service")
    @classmethod
    def _valid_service(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not _SERVICE.match(value):
            raise ValueError("The service must be a compose service name")
        return value


class SoftwareActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    state: str
    service: str | None
    last_error: str | None
    # Who ordered it; only for callers who may see members.
    requested_by: str | None = None
    created_at: datetime
    sent_at: datetime | None
    reported_at: datetime | None


class DeviceActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    state: str
    username: str | None
    sudo: bool
    from_version: str | None = None
    module: str | None = None
    last_error: str | None
    # run_diagnostics: the device's doctor report.
    output: str | None = None
    created_at: datetime
    sent_at: datetime | None
    reported_at: datetime | None


class DeviceActionsResponse(BaseModel):
    # Only the kinds the caller may order (power, users, update_agent and repair).
    actions: list[DeviceActionResponse]


# ------------------------------------------------------------------ agent side
class AgentDeviceAction(BaseModel):
    id: uuid.UUID
    kind: Literal[
        "reboot",
        "shutdown",
        "set_password",
        "add_user",
        "update_agent",
        "restart_module",
        "run_diagnostics",
        "workload_restart",
        "workload_stop",
        "workload_start",
        "workload_snapshot",
    ]
    username: str | None = None
    password: str | None = None
    sudo: bool = False
    authorized_key: str | None = None
    # Version stored when update_agent was ordered (agent reports done when newer).
    from_version: str | None = None
    # restart_module: which module.
    module: str | None = None
    # workload_*: which workload, and which compose service of it (None: all).
    workload_key: str | None = None
    service: str | None = None


class AgentDeviceActionsResponse(BaseModel):
    actions: list[AgentDeviceAction]


class AgentActionResult(BaseModel):
    id: uuid.UUID
    state: Literal["done", "failed"]
    error: str | None = Field(default=None, max_length=500)
    # run_diagnostics: the doctor report. Added in v1 (additive); other kinds leave it out.
    output: str | None = Field(default=None, max_length=MAX_ACTION_OUTPUT)


class AgentActionReportRequest(BaseModel):
    results: list[AgentActionResult] = Field(default_factory=list, max_length=50)


class AgentActionReportResponse(BaseModel):
    accepted: int
