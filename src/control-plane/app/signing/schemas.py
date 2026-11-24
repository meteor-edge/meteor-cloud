"""Signing key schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SigningKeyResponse(BaseModel):
    key_id: str
    algorithm: str
    public_key: str
    endorsed_by: str | None = None
    endorsement: str | None = None
    created_at: datetime
    retired_at: datetime | None = None
