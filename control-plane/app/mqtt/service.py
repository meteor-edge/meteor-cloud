"""MQTT authentication, authorization, status, and ping command handling."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.devices.models import Device, DeviceCommand, DeviceMqttCredential
from app.devices.tokens import hash_token
from app.ports.mqtt import MQTTGateway
from data_plane.connectivity.mqtt.acl import can_access_topic, device_id_from_username
from data_plane.connectivity.mqtt.payloads import parse_json_object, parse_metrics_payload, parse_status_payload
from data_plane.connectivity.mqtt.schemas import (
    DevicePingResponse,
    MqttAuthorizeResponse,
    MqttAuthResponse,
    PingCommandPayload,
)

logger = logging.getLogger(__name__)

MqttPublisher = MQTTGateway


class NoopPublisher:
    def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        raise RuntimeError("MQTT broker is not enabled.")


class MqttService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def authenticate(self, *, username: str, password: str) -> MqttAuthResponse:
        if username == self.settings.mqtt_platform_username:
            if password and password == self.settings.mqtt_platform_password:
                return MqttAuthResponse(result="allow", is_superuser=True)
            return MqttAuthResponse(result="deny")

        device_id = device_id_from_username(username)
        if device_id is None:
            return MqttAuthResponse(result="deny")

        device = self.session.get(Device, device_id)
        if device is None or not device.is_enabled:
            return MqttAuthResponse(result="deny")

        cred = self.session.get(DeviceMqttCredential, device_id)
        if cred is None or cred.revoked_at is not None:
            return MqttAuthResponse(result="deny")
        if cred.password_hash != hash_token(password):
            return MqttAuthResponse(result="deny")
        return MqttAuthResponse(result="allow", is_superuser=False)

    def authorize(self, *, username: str, action: str, topic: str) -> MqttAuthorizeResponse:
        if username == self.settings.mqtt_platform_username:
            return MqttAuthorizeResponse(result="allow")
        device_id = device_id_from_username(username)
        if device_id is None or not can_access_topic(device_id, action, topic):
            return MqttAuthorizeResponse(result="deny")
        return MqttAuthorizeResponse(result="allow")

    def apply_status_message(self, *, device_id: uuid.UUID, payload: str) -> bool:
        status = parse_status_payload(payload)
        if status is None:
            return False
        device = self.session.get(Device, device_id)
        if device is None:
            return False
        now = datetime.now(UTC)
        device.mqtt_status = status
        device.mqtt_status_at = now
        self.session.add(device)
        self.session.flush()
        return True

    def apply_metrics_message(self, *, device_id: uuid.UUID, payload: str) -> bool:
        snapshot = parse_metrics_payload(payload)
        if snapshot is None:
            return False
        device = self.session.get(Device, device_id)
        if device is None:
            return False
        device.mqtt_metrics = snapshot
        device.mqtt_metrics_at = datetime.now(UTC)
        self.session.add(device)
        self.session.flush()
        return True

    def apply_command_result(self, *, device_id: uuid.UUID, payload: str) -> bool:
        data = _parse_json(payload)
        command_id = _as_uuid(data.get("command_id"))
        if command_id is None:
            return False
        command = self.session.get(DeviceCommand, command_id)
        if command is None or command.device_id != device_id:
            logger.info("Rejected MQTT command result for unexpected device")
            return False
        if command.status == "completed":
            return True
        status = data.get("status")
        if status not in {"completed", "failed"}:
            status = "completed"
        command.status = status
        command.result = data.get("result") if isinstance(data.get("result"), dict) else data
        command.completed_at = datetime.now(UTC)
        self.session.add(command)
        self.session.flush()
        return True

    def send_ping(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        publisher: MqttPublisher,
    ) -> DevicePingResponse:
        device = self.session.get(Device, device_id)
        if device is None or device.organization_id != organization_id:
            raise NotFoundError("device_not_found", "Device was not found.")
        cred = self.session.get(DeviceMqttCredential, device_id)
        if cred is None or cred.revoked_at is not None:
            raise ConflictError(
                "mqtt_not_configured",
                "This device has no active MQTT credential.",
            )

        command = DeviceCommand(
            organization_id=organization_id,
            device_id=device_id,
            type="ping",
            status="pending",
        )
        self.session.add(command)
        self.session.commit()
        self.session.refresh(command)

        command.status = "sent"
        self.session.add(command)
        self.session.commit()
        self.session.refresh(command)

        started = time.monotonic()
        payload = PingCommandPayload(command_id=command.id).model_dump_json()
        try:
            publisher.publish(
                f"devices/{device_id}/commands",
                payload,
                qos=1,
                retain=False,
            )
        except Exception:
            self.session.refresh(command)
            if command.status not in {"completed", "failed"}:
                command.status = "failed"
                command.completed_at = datetime.now(UTC)
                self.session.add(command)
                self.session.commit()
            return DevicePingResponse(
                command_id=command.id,
                status="failed",
                message="Could not publish ping to the MQTT broker.",
            )

        deadline = time.monotonic() + self.settings.mqtt_ping_timeout_seconds
        while time.monotonic() < deadline:
            self.session.expire(command)
            self.session.refresh(command)
            if command.status in {"completed", "failed"}:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return DevicePingResponse(
                    command_id=command.id,
                    status=command.status,
                    round_trip_ms=elapsed_ms if command.status == "completed" else None,
                    result=command.result,
                )
            time.sleep(0.05)

        self.session.refresh(command)
        if command.status in {"completed", "failed"}:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return DevicePingResponse(
                command_id=command.id,
                status=command.status,
                round_trip_ms=elapsed_ms if command.status == "completed" else None,
                result=command.result,
            )
        command.status = "failed"
        command.completed_at = datetime.now(UTC)
        self.session.add(command)
        self.session.commit()
        return DevicePingResponse(
            command_id=command.id,
            status="failed",
            message="Timed out waiting for pong.",
        )


def _parse_json(payload: str) -> dict[str, Any]:
    return parse_json_object(payload)


def _as_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
