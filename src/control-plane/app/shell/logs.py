"""Device log sources and the read-only log command run over SSH.

The agent reports sources (``meteorcli-logs list`` on the device); the console
picks one and the control plane builds the command from validated fields only.
The device-side helper checks everything again before it prints anything.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

# Root-owned helper installed by the agent's installcli.sh, behind a sudo rule for the device user.
LOGS_HELPER = "/usr/local/libexec/meteorcli-logs"
MAX_SOURCES = 300
MAX_LINES = 5000
MAX_GREP = 200
SINCE_VALUES = ("15m", "1h", "6h", "24h", "7d")

LogKind = Literal["journal", "systemd", "docker", "file"]

_NAMES = {
    "systemd": re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:\\-]{0,199}\.service$"),
    "docker": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"),
    "file": re.compile(r"^/var/log/[A-Za-z0-9._/-]{1,200}$"),
}


def _check_name(kind: str, name: str) -> str:
    if kind == "journal":
        if name:
            raise ValueError("journal has no name")
        return name
    if not _NAMES[kind].match(name) or "/../" in name or name.endswith("/.."):
        raise ValueError(f"invalid {kind} name")
    return name


class LogSource(BaseModel):
    kind: LogKind
    name: str = Field(default="", max_length=255)
    state: str | None = Field(default=None, max_length=64)
    project: str | None = Field(default=None, max_length=64)
    size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _valid_name(self) -> LogSource:
        _check_name(self.kind, self.name)
        return self


def clean_log_sources(raw: list[Any]) -> list[dict[str, Any]]:
    """Keep the well-formed sources an agent reported (others are dropped, not fatal)."""
    sources: list[dict[str, Any]] = []
    for item in raw[:MAX_SOURCES]:
        try:
            sources.append(LogSource.model_validate(item).model_dump(exclude_none=True))
        except ValidationError:
            continue
    return sources


class LogSourcesResponse(BaseModel):
    """``reported_at`` is None when the agent never reported (too old, or no helper)."""

    sources: list[LogSource]
    reported_at: datetime | None
    deployed: list[str] = Field(
        default_factory=list,
        description="Source keys (kind:name) of workloads deployed by MeteorCloud.",
    )


class LogSessionCreateRequest(BaseModel):
    kind: LogKind
    name: str = Field(default="", max_length=255)
    lines: int = Field(default=500, ge=1, le=MAX_LINES)
    since: Literal["15m", "1h", "6h", "24h", "7d"] | None = None
    grep: str | None = Field(default=None, max_length=MAX_GREP)
    follow: bool = True

    @field_validator("grep")
    @classmethod
    def _printable(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("grep must be printable text")
        return value

    @model_validator(mode="after")
    def _valid_name(self) -> LogSessionCreateRequest:
        _check_name(self.kind, self.name)
        return self


def source_key(kind: str, name: str) -> str:
    return kind if kind == "journal" else f"{kind}:{name}"


def log_command(params: dict[str, Any]) -> list[str]:
    """argv the device runs (via sudo) for a validated log session."""
    request = LogSessionCreateRequest.model_validate(params)
    argv = ["sudo", "-n", LOGS_HELPER, "show", request.kind]
    if request.kind != "journal":
        argv.append(request.name)
    argv += ["--lines", str(request.lines)]
    if request.since:
        argv += ["--since", request.since]
    if request.grep:
        argv += ["--grep", request.grep]
    if request.follow:
        argv.append("--follow")
    return argv


def deployed_keys(workloads: list[Any]) -> list[str]:
    """Source keys for MeteorCloud workloads (``compose:<project>`` covers a project's containers)."""
    keys: list[str] = []
    for row in workloads:
        spec = row.desired_spec or {}
        if row.kind == "systemd":
            unit = str(spec.get("unit_name") or row.workload_key)
            keys.append(f"systemd:{unit if unit.endswith('.service') else unit + '.service'}")
        elif row.kind == "docker":
            keys.append(f"docker:{spec.get('container_name') or row.workload_key}")
        elif row.kind == "docker_compose":
            keys.append(f"compose:{spec.get('project_name') or row.workload_key}")
    return keys
