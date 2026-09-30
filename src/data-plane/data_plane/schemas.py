"""HTTP request models for the data-plane MQTT API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    topic: str
    payload: str
    qos: int = Field(default=1, ge=0, le=2)
    retain: bool = False


class SubscriptionRequest(BaseModel):
    topic: str
