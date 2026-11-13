"""VerneMQ webhook adapter (``vmq_webhooks``).

VerneMQ posts its own JSON shapes and expects its own answers. These endpoints
only translate; every decision is made by the same ``MqttService`` and topic
policy that the Mosquitto plugin and EMQX use.

VerneMQ webhooks cannot send custom headers, so the shared secret travels in
the endpoint URL: ``.../internal/mqtt/vernemq/<hook>?token=<MQTT_INTERNAL_TOKEN>``.
Keep that URL on the private network between broker and control plane.

No ``cache-control`` header is returned, so VerneMQ asks again for every
publish and subscribe and a revoked device loses access immediately.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.mqtt.service import MqttService

router = APIRouter(prefix="/internal/mqtt/vernemq", tags=["internal-mqtt"], include_in_schema=False)

_OK: dict[str, Any] = {"result": "ok"}
_DENY: dict[str, Any] = {"result": {"error": "not_allowed"}}
#: SUBACK failure code VerneMQ accepts per topic in an ``auth_on_subscribe`` answer.
_SUBSCRIBE_DENIED_QOS = 128


def _require_token(
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str | None, Query()] = None,
) -> None:
    expected = settings.mqtt_internal_token
    if not expected or token is None or not hmac.compare_digest(token, expected):
        raise UnauthorizedError("mqtt_internal_forbidden", "Invalid MQTT internal token.")


class _RegisterHook(BaseModel):
    username: str | None = None
    password: str | None = None
    client_id: str = ""


class _PublishHook(BaseModel):
    username: str | None = None
    client_id: str = ""
    topic: str = ""
    retain: bool = False


class _SubscribeTopic(BaseModel):
    topic: str = ""
    qos: int = 0


class _SubscribeHook(BaseModel):
    username: str | None = None
    client_id: str = ""
    topics: list[_SubscribeTopic] = []


Service = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]
Guard = Annotated[None, Depends(_require_token)]


def _register(payload: _RegisterHook, session: Session, settings: Settings) -> dict[str, Any]:
    result = MqttService(session, settings=settings).authenticate(
        username=payload.username or "",
        password=payload.password or "",
        clientid=payload.client_id,
    )
    return _OK if result.result == "allow" else _DENY


def _publish(payload: _PublishHook, session: Session, settings: Settings) -> dict[str, Any]:
    result = MqttService(session, settings=settings).authorize(
        username=payload.username or "",
        action="publish",
        topic=payload.topic,
        clientid=payload.client_id,
        retain=payload.retain,
    )
    return _OK if result.result == "allow" else _DENY


def _subscribe(payload: _SubscribeHook, session: Session, settings: Settings) -> dict[str, Any]:
    service = MqttService(session, settings=settings)
    if not payload.topics:
        return _DENY
    answered = []
    for item in payload.topics:
        allowed = (
            service.authorize(
                username=payload.username or "",
                action="subscribe",
                topic=item.topic,
                clientid=payload.client_id,
            ).result
            == "allow"
        )
        answered.append({"topic": item.topic, "qos": item.qos if allowed else _SUBSCRIBE_DENIED_QOS})
    # Per-topic answers: allowed filters keep their QoS, denied ones get a failure SUBACK.
    return {"result": "ok", "topics": answered}


# MQTT 3.1.1 and MQTT 5 hooks carry the same fields we need.
@router.post("/auth_on_register")
@router.post("/auth_on_register_m5")
def vernemq_auth_on_register(payload: _RegisterHook, session: Service, settings: Config, _: Guard) -> dict[str, Any]:
    return _register(payload, session, settings)


@router.post("/auth_on_publish")
@router.post("/auth_on_publish_m5")
def vernemq_auth_on_publish(payload: _PublishHook, session: Service, settings: Config, _: Guard) -> dict[str, Any]:
    return _publish(payload, session, settings)


@router.post("/auth_on_subscribe")
@router.post("/auth_on_subscribe_m5")
def vernemq_auth_on_subscribe(payload: _SubscribeHook, session: Service, settings: Config, _: Guard) -> dict[str, Any]:
    return _subscribe(payload, session, settings)
