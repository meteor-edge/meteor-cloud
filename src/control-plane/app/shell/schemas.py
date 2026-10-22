"""Shell session API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ShellSessionCreateResponse(BaseModel):
    session_id: uuid.UUID
    ticket: str
    websocket_path: str
    expires_at: datetime
    device_id: uuid.UUID


class ShellSessionResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    status: str
    created_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None
