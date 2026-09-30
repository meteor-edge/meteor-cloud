"""Audit event schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    request_id: str | None
    ip: str | None
    outcome: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime
