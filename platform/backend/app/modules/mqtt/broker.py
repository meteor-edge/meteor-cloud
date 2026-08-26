"""Platform MQTT client: publish commands and ingest status/results."""

from __future__ import annotations

import logging
import ssl
import uuid
from datetime import UTC, datetime
from threading import Event, Lock

import paho.mqtt.client as mqtt

from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.fleet.models import Device
from app.modules.mqtt.acl import parse_device_topic
from app.modules.mqtt.hub import MqttTestEvent, get_mqtt_event_hub
from app.modules.mqtt.service import MqttPublisher, MqttService, NoopPublisher

logger = logging.getLogger(__name__)

_SUBSCRIBE_TOPICS = (
    "devices/+/status",
    "devices/+/metrics",
    "devices/+/commands/result",
    "devices/+/events",
)


class PlatformMqttClient(MqttPublisher):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: mqtt.Client | None = None
        self._ready = Event()
        self._pending_subs = 0
        self._lock = Lock()
        self._watch_counts: dict[str, int] = {}

    def start(self) -> None:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"edge-platform-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(
            self.settings.mqtt_platform_username,
            self.settings.mqtt_platform_password,
        )
        ca_path = self.settings.mqtt_ca_cert_path
        client.tls_set(ca_certs=ca_path, cert_reqs=ssl.CERT_REQUIRED)
        client.tls_insecure_set(False)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message
        client.connect_async(self.settings.mqtt_broker_host, self.settings.mqtt_broker_port, 60)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        client = self._client
        self._ready.clear()
        if client is None:
            return
        client.loop_stop()
        client.disconnect()
        self._client = None

    def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        client = self._client
        if client is None:
            raise RuntimeError("MQTT client is not connected.")
        if not self._ready.wait(timeout=15):
            raise RuntimeError("MQTT client is not subscribed.")
        info = client.publish(topic, payload=payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise RuntimeError("MQTT publish did not complete.")

    def watch_topic(self, topic: str) -> None:
        client = self._client
        with self._lock:
            self._watch_counts[topic] = self._watch_counts.get(topic, 0) + 1
            first = self._watch_counts[topic] == 1
        if not first or topic in _SUBSCRIBE_TOPICS or client is None:
            return
        connected = client.is_connected()
        if connected:
            with self._lock:
                self._pending_subs += 1
        result, _mid = client.subscribe(topic, qos=1)
        if connected and result != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("MQTT platform subscribe request failed for %s: %s", topic, result)
            self._account_subscribe()

    def unwatch_topic(self, topic: str) -> None:
        client = self._client
        with self._lock:
            remaining = self._watch_counts.get(topic, 0) - 1
            if remaining <= 0:
                self._watch_counts.pop(topic, None)
                last = True
            else:
                self._watch_counts[topic] = remaining
                last = False
        if last and topic not in _SUBSCRIBE_TOPICS and client is not None:
            client.unsubscribe(topic)

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: object,
        _connect_flags: object,
        reason_code: object,
        _properties: object = None,
    ) -> None:
        if str(reason_code) not in {"Success", "0"}:
            logger.warning("MQTT platform client connect failed: %s", reason_code)
            self._ready.clear()
            return
        self._ready.clear()
        with self._lock:
            extra = [name for name, count in self._watch_counts.items() if count > 0]
            self._pending_subs = len(_SUBSCRIBE_TOPICS) + sum(
                1 for name in extra if name not in _SUBSCRIBE_TOPICS
            )
        for topic in _SUBSCRIBE_TOPICS:
            client.subscribe(topic, qos=1)
        for topic in extra:
            if topic not in _SUBSCRIBE_TOPICS:
                client.subscribe(topic, qos=1)
        logger.info("MQTT platform client connected")

    def _on_subscribe(
        self,
        _client: mqtt.Client,
        _userdata: object,
        _mid: int,
        reason_codes: object,
        _properties: object = None,
    ) -> None:
        codes = reason_codes if isinstance(reason_codes, list) else [reason_codes]
        if any(bool(getattr(code, "is_failure", False)) for code in codes):
            logger.warning("MQTT platform subscribe failed: %s", codes)
        self._account_subscribe()

    def _account_subscribe(self) -> None:
        with self._lock:
            self._pending_subs = max(0, self._pending_subs - 1)
            ready = self._pending_subs == 0
        if ready:
            self._ready.set()

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: object,
        _flags: object,
        _reason_code: object,
        _properties: object = None,
    ) -> None:
        self._ready.clear()

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        parsed = parse_device_topic(message.topic)
        payload = message.payload.decode("utf-8", errors="replace")
        session = SessionLocal()
        try:
            service = MqttService(session, settings=self.settings)
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
            _fanout_event(
                organization_id=org_id,
                device_id=device_id,
                topic=message.topic,
                payload=payload,
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Failed to handle MQTT message on %s", message.topic)
        finally:
            session.close()


def _fanout_event(
    *,
    organization_id: uuid.UUID | None,
    device_id: uuid.UUID | None,
    topic: str,
    payload: str,
) -> None:
    get_mqtt_event_hub().publish(
        MqttTestEvent(
            organization_id=organization_id,
            device_id=device_id,
            topic=topic,
            payload=payload,
            received_at=datetime.now(UTC).isoformat(),
        )
    )


_publisher: MqttPublisher | None = None
_platform_client: PlatformMqttClient | None = None


def get_mqtt_topic_watcher() -> PlatformMqttClient | None:
    return _platform_client


def get_mqtt_publisher() -> MqttPublisher:
    global _publisher
    if _publisher is None:
        _publisher = NoopPublisher()
    return _publisher


def set_mqtt_publisher(publisher: MqttPublisher) -> None:
    global _publisher
    _publisher = publisher


def start_mqtt_runtime(settings: Settings) -> None:
    global _publisher, _platform_client
    if not settings.mqtt_enabled:
        _publisher = NoopPublisher()
        return
    client = PlatformMqttClient(settings)
    try:
        client.start()
    except Exception:
        logger.exception("MQTT platform client failed to start; commands will be unavailable")
        _publisher = NoopPublisher()
        return
    _platform_client = client
    _publisher = client


def stop_mqtt_runtime() -> None:
    global _platform_client, _publisher
    if _platform_client is not None:
        _platform_client.stop()
        _platform_client = None
    _publisher = NoopPublisher()
