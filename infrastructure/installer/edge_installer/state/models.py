"""Installer state models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class InstallationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    provider: str
    environment: str
    region: str
    instance_id: str | None = None
    public_ip: str | None = None
    elastic_ip: str | None = None
    platform_url: str | None = None
    platform_version: str
    installed_components: list[str] = Field(default_factory=list)
    enabled_services: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
