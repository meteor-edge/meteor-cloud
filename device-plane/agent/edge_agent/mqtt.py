"""Device MQTT session: TLS connect, ping/pong, LWT, reconnect backoff."""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event, Thread

import paho.mqtt.client as mqtt

from edge_agent.metrics import collect_metrics, read_uptime_seconds
from edge_agent.mqtt_config import MqttConfig, resolve_mqtt_broker_host

logger = logging.getLogger("edge_agent")

AGENT_VERSION = "0.2.0"
COMMANDS_QOS = 1
STATUS_QOS = 1
METRICS_INTERVAL_SECONDS = 60.0
DEVICE_PUBLISH_SUFFIXES = frozenset({"status", "metrics", "commands/result"})
DEVICE_SUBSCRIBE_SUFFIXES = frozenset({"commands"})


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def online_status_payload() -> dict:
    payload: dict = {
        "status": "online",
        "timestamp": utc_timestamp(),
        "agent_version": AGENT_VERSION,
    }
    uptime = read_uptime_seconds()
    if uptime is not None:
        payload["uptime_seconds"] = uptime
    return payload


def next_backoff(seconds: float, *, factor: float = 2.0, max_delay: float = 30.0) -> float:
    return min(max(seconds * factor, 1.0), max_delay)


def handle_command(payload: str) -> dict | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "ping":
        return None
    command_id = data.get("command_id")
    if not command_id:
        return None
    return {
        "command_id": command_id,
        "status": "completed",
        "result": {"message": "pong"},
    }


def tls_insecure_enabled() -> bool:
    raw = os.environ.get("METEORCLI_MQTT_TLS_INSECURE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def verify_broker_tls(
    host: str,
    port: int,
    ca_path: str | None,
    *,
    insecure: bool = False,
    timeout: float = 8,
) -> None:
    """Fail fast on unreachable brokers or TLS hostname/CA mismatch."""
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise RuntimeError(f"MQTT broker {host}:{port} is not reachable: {exc}") from exc
    try:
        if insecure:
            ctx = ssl._create_unverified_context()
        elif ca_path:
            ctx = ssl.create_default_context(cafile=ca_path)
        else:
            ctx = ssl.create_default_context()
        ctx.wrap_socket(raw, server_hostname=host)
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            f"MQTT TLS verification failed for {host}:{port}: {exc}. "
            "The broker certificate SAN must include that address "
            "(set MQTT_PUBLIC_HOST, run make mqtt-certs, restart EMQX)."
        ) from exc
    finally:
        raw.close()


class DeviceMqttSession:
    def __init__(
        self,
        device_id: str,
        config: MqttConfig,
        *,
        tls_insecure: bool | None = None,
        server_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.device_id = device_id
        self.config = config
        self.tls_insecure = tls_insecure_enabled() if tls_insecure is None else tls_insecure
        self.server_url = server_url
        self._sleep = sleep
        self._client: mqtt.Client | None = None
        self._metrics_stop = Event()
        self._metrics_thread: Thread | None = None

    def _broker_host(self) -> str:
        return resolve_mqtt_broker_host(self.config.host, self.server_url)

    @property
    def commands_topic(self) -> str:
        return f"devices/{self.device_id}/commands"

    @property
    def status_topic(self) -> str:
        return f"devices/{self.device_id}/status"

    @property
    def metrics_topic(self) -> str:
        return f"devices/{self.device_id}/metrics"

    @property
    def result_topic(self) -> str:
        return f"devices/{self.device_id}/commands/result"

    def start(self) -> None:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"device-{self.device_id}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self.config.username, self.config.password)
        if self.config.tls:
            self._configure_tls(client)
        client.will_set(
            self.status_topic,
            payload=json.dumps({"status": "offline"}),
            qos=STATUS_QOS,
            retain=True,
        )
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        logger.info("Connecting to MQTT %s:%s with TLS", self._broker_host(), self.config.port)
        verify_broker_tls(
            self._broker_host(),
            self.config.port,
            self.config.ca_path,
            insecure=self.tls_insecure,
        )
        client.connect_async(self._broker_host(), self.config.port, 60)
        client.loop_start()
        self._client = client
        self._metrics_stop.clear()
        self._metrics_thread = Thread(target=self._metrics_loop, name="mqtt-metrics", daemon=True)
        self._metrics_thread.start()

    def stop(self) -> None:
        client = self._client
        self._metrics_stop.set()
        if client is None:
            return
        try:
            info = client.publish(
                self.status_topic,
                payload=json.dumps({"status": "offline"}),
                qos=STATUS_QOS,
                retain=True,
            )
            info.wait_for_publish(timeout=2.0)
            if not info.is_published():
                logger.debug("Could not publish MQTT offline status during stop")
        except Exception:
            logger.debug("Could not publish MQTT offline status during stop")
        client.disconnect()
        client.loop_stop()
        self._client = None

    def _configure_tls(self, client: mqtt.Client) -> None:
        if self.tls_insecure:
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
            return
        client.tls_set(ca_certs=self.config.ca_path, cert_reqs=ssl.CERT_REQUIRED)
        client.tls_insecure_set(False)

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: object,
        _connect_flags: object,
        reason_code: object,
        _properties: object = None,
    ) -> None:
        failed = getattr(reason_code, "is_failure", False)
        if failed:
            logger.warning("MQTT connect failed")
            return
        client.subscribe(self.commands_topic, qos=COMMANDS_QOS)
        client.publish(
            self.status_topic,
            payload=json.dumps(online_status_payload()),
            qos=STATUS_QOS,
            retain=True,
        )
        logger.info("MQTT connected; subscribed to commands")

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: object,
        _disconnect_flags: object,
        reason_code: object,
        _properties: object = None,
    ) -> None:
        if str(reason_code) in {"Success", "0"}:
            return
        logger.warning("MQTT disconnected; paho will reconnect with backoff")

    def _on_message(
        self,
        client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        result = handle_command(payload)
        if result is None:
            return
        client.publish(
            self.result_topic,
            payload=json.dumps(result),
            qos=COMMANDS_QOS,
            retain=False,
        )

    def _metrics_loop(self) -> None:
        while not self._metrics_stop.is_set():
            self._publish_metrics()
            if self._metrics_stop.wait(METRICS_INTERVAL_SECONDS):
                break

    def _publish_metrics(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            body = collect_metrics()
            body["timestamp"] = utc_timestamp()
            client.publish(
                self.metrics_topic,
                payload=json.dumps(body),
                qos=STATUS_QOS,
                retain=False,
            )
        except Exception:
            logger.debug("Could not publish MQTT metrics")


def commands_topic(device_id: str) -> str:
    return f"devices/{device_id}/commands"


DEFAULT_PUBLISH_SUFFIX = "status"
DEFAULT_SUBSCRIBE_SUFFIX = "commands"


def normalize_device_topic(
    device_id: str,
    topic: str | None,
    *,
    action: str = "publish",
) -> str:
    """Resolve a CLI topic to an allowed devices/{device_id}/… name."""
    allowed = DEVICE_PUBLISH_SUFFIXES if action == "publish" else DEVICE_SUBSCRIBE_SUFFIXES
    default = DEFAULT_PUBLISH_SUFFIX if action == "publish" else DEFAULT_SUBSCRIBE_SUFFIX
    raw = (topic or default).strip() or default
    if any(part in {"+", "#"} for part in raw.split("/")):
        raise ValueError("MQTT wildcards are not allowed")
    prefix = f"devices/{device_id}/"
    if raw.startswith("devices/"):
        if not raw.startswith(prefix):
            raise ValueError(f"topic must be under {prefix.rstrip('/')}")
        resolved = raw
    else:
        resolved = f"{prefix}{raw}"
    suffix = resolved[len(prefix) :]
    if suffix not in allowed:
        names = ", ".join(sorted(allowed))
        raise ValueError(f"topic must be one of: {names}")
    return resolved


def _device_mqtt_client(
    device_id: str,
    config: MqttConfig,
    *,
    suffix: str,
    insecure: bool,
) -> mqtt.Client:
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"device-{device_id}-{suffix}-{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(config.username, config.password)
    if config.tls:
        if insecure:
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
        else:
            client.tls_set(ca_certs=config.ca_path, cert_reqs=ssl.CERT_REQUIRED)
            client.tls_insecure_set(False)
    return client


def _wait_for_connect(
    client: mqtt.Client,
    host: str,
    port: int,
    timeout: float,
) -> None:
    connected = Event()
    connect_result: dict[str, object] = {}

    def _on_connect(
        _client: mqtt.Client,
        _userdata: object,
        _connect_flags: object,
        reason_code: object,
        _properties: object = None,
    ) -> None:
        connect_result["reason_code"] = reason_code
        connected.set()

    def _on_disconnect(
        _client: mqtt.Client,
        _userdata: object,
        _disconnect_flags: object,
        reason_code: object,
        _properties: object = None,
    ) -> None:
        if connected.is_set():
            return
        connect_result["disconnect"] = reason_code
        connected.set()

    client.reconnect_on_failure = False
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    logger.info("Connecting to MQTT %s:%s with TLS", host, port)
    client.loop_start()
    client.connect_async(host, port, 30)
    if not connected.wait(timeout):
        raise RuntimeError(f"MQTT connect timed out ({host}:{port})")
    reason_code = connect_result.get("reason_code")
    if getattr(reason_code, "is_failure", False):
        raise RuntimeError(
            f"MQTT connect refused: {reason_code}. "
            "If this device was re-registered, mqtt.json has a stale password."
        )
    if "disconnect" in connect_result and reason_code is None:
        raise RuntimeError(
            f"MQTT connection closed before CONNACK ({connect_result['disconnect']}). "
            "Check the MQTT password in mqtt.json, or re-register the device."
        )


def publish_test_event(
    device_id: str,
    config: MqttConfig,
    payload: dict | str,
    *,
    topic: str | None = None,
    tls_insecure: bool | None = None,
    server_url: str | None = None,
    timeout: float = 12,
) -> str:
    """Connect over TLS, publish one message, then disconnect."""
    insecure = tls_insecure_enabled() if tls_insecure is None else tls_insecure
    host = resolve_mqtt_broker_host(config.host, server_url)
    resolved = normalize_device_topic(device_id, topic, action="publish")
    body = json.dumps(payload) if isinstance(payload, dict) else payload
    verify_broker_tls(host, config.port, config.ca_path, insecure=insecure)
    client = _device_mqtt_client(device_id, config, suffix="mqtt-test", insecure=insecure)
    try:
        _wait_for_connect(client, host, config.port, timeout)
        info = client.publish(resolved, body, qos=COMMANDS_QOS, retain=False)
        info.wait_for_publish(timeout=8)
        if not info.is_published():
            raise RuntimeError("MQTT publish did not complete")
    finally:
        client.disconnect()
        client.loop_stop()
    return resolved


def listen_mqtt(
    device_id: str,
    config: MqttConfig,
    *,
    topic: str | None = None,
    tls_insecure: bool | None = None,
    server_url: str | None = None,
    timeout: float | None = None,
    connect_timeout: float = 12,
    on_message: Callable[[str, str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Subscribe to this device's command topic over TLS.

    Default topic is ``devices/{id}/commands`` (the only device subscribe).
    Uses a unique client id so ``meteorcli run`` stays connected.
    """
    insecure = tls_insecure_enabled() if tls_insecure is None else tls_insecure
    host = resolve_mqtt_broker_host(config.host, server_url)
    resolved = normalize_device_topic(device_id, topic, action="subscribe")
    emit = on_message or (lambda _topic, _payload: None)
    verify_broker_tls(host, config.port, config.ca_path, insecure=insecure)
    client = _device_mqtt_client(device_id, config, suffix="mqtt-listen", insecure=insecure)

    def _on_message(
        _client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        emit(message.topic, payload)

    client.on_message = _on_message
    try:
        _wait_for_connect(client, host, config.port, connect_timeout)
        result, _mid = client.subscribe(resolved, qos=COMMANDS_QOS)
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT subscribe failed for {resolved}")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if timeout is not None and (timeout <= 0 or time.monotonic() >= deadline):
                break
            sleep(0.1)
    finally:
        client.disconnect()
        client.loop_stop()
    return resolved
