"""Apply inbound MQTT payloads to device records and the test UI hub."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.devices.models import Device
from app.mqtt.acl import parse_device_topic
from app.mqtt.hub import MqttTestEvent, get_mqtt_event_hub
from app.mqtt.service import MqttService


def ingest_mqtt_message(session: Session, settings: Settings, *, topic: str, payload: str) -> None:
    parsed = parse_device_topic(topic)
    service = MqttService(session, settings=settings)
    org_id = None
    device_id = None
    if parsed is not None:
        device_id, suffix = parsed
        if suffix == "status":
            service.apply_status_message(device_id=device_id, payload=payload)
        elif suffix == "metrics":
            service.apply_metrics_message(device_id=device_id, payload=payload)
        elif suffix == "commands/result":
            service.apply_command_result(device_id=device_id, payload=payload)
        device = session.get(Device, device_id)
        if device is not None:
            org_id = device.organization_id
    get_mqtt_event_hub().publish(
        MqttTestEvent(
            organization_id=org_id,
            device_id=device_id,
            topic=topic,
            payload=payload,
            received_at=datetime.now(UTC).isoformat(),
        )
    )
    session.commit()
