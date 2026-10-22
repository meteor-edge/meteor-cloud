"""Schemas for device software (desired vs installed)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.deployments.software_models import SoftwareHealth, SoftwareSyncStatus
from app.releases.kinds import ReleaseKind


class ReleaseSummary(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    kind: ReleaseKind


class DeviceSoftwareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    workload_key: str
    kind: ReleaseKind
    desired_release_id: uuid.UUID | None
    desired_revision: int
    desired_spec: dict[str, Any]
    desired_release: ReleaseSummary | None = None
    installed_release_id: uuid.UUID | None
    installed_version: str | None
    installed_release: ReleaseSummary | None = None
    health: str
    sync_status: str
    progress_percent: int
    attempt_count: int
    last_error: str | None
    reported_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DesiredSoftwareItem(BaseModel):
    """What the agent should install for one workload."""

    workload_key: str
    kind: ReleaseKind
    release_id: uuid.UUID
    release_name: str
    release_version: str
    artifact_id: uuid.UUID | None
    revision: int
    spec: dict[str, Any]


class SoftwareReportItem(BaseModel):
    """Agent-reported status for one workload."""

    workload_key: str
    installed_release_id: uuid.UUID | None = None
    installed_version: str | None = Field(default=None, max_length=64)
    health: SoftwareHealth = SoftwareHealth.UNKNOWN
    sync_status: SoftwareSyncStatus | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    last_error: str | None = Field(default=None, max_length=2000)

    @field_validator("installed_version")
    @classmethod
    def _strip_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None
