"""Internal MQTT authenticate/authorize endpoints used by the broker.

The Mosquitto auth plugin and EMQX's HTTP authn/authz both call these. They are
not user APIs; the broker must send ``X-MQTT-Internal-Token``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.mqtt.ingest import ingest_mqtt_message
from app.mqtt.schemas import (
    MqttAuthorizeRequest,
    MqttAuthorizeResponse,
    MqttAuthRequest,
    MqttAuthResponse,
    MqttIngestRequest,
    MqttIngestResponse,
)
from app.mqtt.service import MqttService

router = APIRouter(prefix="/internal/mqtt", tags=["internal-mqtt"], include_in_schema=False)


def _require_internal_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_mqtt_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.mqtt_internal_token
    if not expected or x_mqtt_internal_token != expected:
        raise UnauthorizedError("mqtt_internal_forbidden", "Invalid MQTT internal token.")


def _as_bool(value: bool | str | None) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1"}
    return bool(value)


@router.post("/authenticate", response_model=MqttAuthResponse)
def mqtt_authenticate(
    payload: MqttAuthRequest,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(_require_internal_token)],
) -> MqttAuthResponse:
    return MqttService(session, settings=settings).authenticate(
        username=payload.username,
        password=payload.password,
        clientid=payload.clientid,
    )


@router.post("/authorize", response_model=MqttAuthorizeResponse)
def mqtt_authorize(
    payload: MqttAuthorizeRequest,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(_require_internal_token)],
) -> MqttAuthorizeResponse:
    return MqttService(session, settings=settings).authorize(
        username=payload.username,
        action=payload.action,
        topic=payload.topic,
        clientid=payload.clientid,
        retain=_as_bool(payload.retain),
    )


@router.post("/ingest", response_model=MqttIngestResponse)
def mqtt_ingest(
    payload: MqttIngestRequest,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(_require_internal_token)],
) -> MqttIngestResponse:
    ingest_mqtt_message(session, settings, topic=payload.topic, payload=payload.payload)
    return MqttIngestResponse(ok=True)
