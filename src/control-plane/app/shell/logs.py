"""Device log sources and the read-only log commands run over SSH.

The console asks for the sources when the Logs window opens (``meteorcli-logs list``
on the device), picks one, and the control plane builds the ``show`` command from
these fields. The device-side helper is the security boundary: it checks the name
against what it lists right now and enforces its own limits, so only basic shape
checks are repeated here.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

# Root-owned helper installed by the agent's installcli.sh, behind a sudo rule for the device user.
LOGS_HELPER = "/usr/local/libexec/meteorcli-logs"
LIST_COMMAND = ["sudo", "-n", LOGS_HELPER, "list"]
MAX_LINES = 10000
MAX_GREP = 200

LogKind = Literal["journal", "systemd", "docker", "file"]


def _printable(value: str) -> bool:
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


class LogSource(BaseModel):
    kind: LogKind
    name: str = Field(default="", max_length=255)
    state: str | None = Field(default=None, max_length=64)
    project: str | None = Field(default=None, max_length=64)
    size: int | None = Field(default=None, ge=0)


def parse_log_sources(output: bytes) -> list[LogSource] | None:
    """Sources from ``meteorcli-logs list`` output; None when it is not a JSON list."""
    try:
        raw = json.loads(output.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(raw, list):
        return None
    sources: list[LogSource] = []
    for item in raw:
        try:
            sources.append(LogSource.model_validate(item))
        except ValidationError:
            continue
    return sources


class LogSourcesResponse(BaseModel):
    sources: list[LogSource]
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
    def _printable_grep(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not _printable(value):
            raise ValueError("grep must be printable text")
        return value

    @model_validator(mode="after")
    def _valid_name(self) -> LogSessionCreateRequest:
        if self.kind == "journal":
            if self.name:
                raise ValueError("journal has no name")
        elif not self.name or self.name.startswith("-") or not _printable(self.name):
            raise ValueError(f"invalid {self.kind} name")
        return self


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
