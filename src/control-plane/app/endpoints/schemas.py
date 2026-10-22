"""Application endpoint API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EndpointCreate(BaseModel):
    device_id: uuid.UUID
    hostname: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    protocol: str = "http"
    deployment_id: uuid.UUID | None = None
    workload_key: str | None = Field(default=None, max_length=255)
    enabled: bool = True

    @field_validator("hostname")
    @classmethod
    def _normalize_hostname(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, value: str) -> str:
        protocol = value.strip().lower()
        if protocol not in {"http", "https"}:
            raise ValueError("protocol must be http or https")
        return protocol

    @field_validator("workload_key")
    @classmethod
    def _strip_workload(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class EndpointUpdate(BaseModel):
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str | None = None
    enabled: bool | None = None

    @field_validator("hostname")
    @classmethod
    def _normalize_hostname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        protocol = value.strip().lower()
        if protocol not in {"http", "https"}:
            raise ValueError("protocol must be http or https")
        return protocol


class EndpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    deployment_id: uuid.UUID | None
    device_software_id: uuid.UUID | None
    workload_key: str | None
    hostname: str
    protocol: str
    port: int
    enabled: bool
    availability: str
    public_url: str
    upstream_url: str | None
    created_at: datetime
    updated_at: datetime
