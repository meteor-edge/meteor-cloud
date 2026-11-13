"""paho-mqtt helpers. Passwords are never logged."""

from __future__ import annotations

import json
import ssl
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event

import paho.mqtt.client as mqtt


def ping_reply(payload: str) -> dict | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "ping":
        return None
    command_id = data.get("command_id")
    if not command_id:
        return None
    return {"command_id": command_id, "status": "completed", "result": {"message": "pong"}}


@dataclass
class MqttSession:
    host: str
    port: int
    ca_file: str
    username: str | None = None
    password: str | None = None
    # Devices must use ``device-<id>[-suffix]`` (the broker refuses anything else);
    # left empty, start() picks a valid one.
    client_id: str | None = None
    untrusted_ca: str | None = None
    device_id: str | None = None
    auto_subscribe: list[str] = field(default_factory=list)
    handle_ping: bool = False
    messages: list[tuple[str, str]] = field(default_factory=list)
    connected: Event = field(default_factory=Event)
    subscribed: Event = field(default_factory=Event)
    subscribe_ok: bool | None = None
    connect_ok: bool | None = None
    connect_count: int = 0
    disconnect_event: Event = field(default_factory=Event)
    _client: mqtt.Client | None = None

    def start(self, *, lwt_topic: str | None = None, lwt_payload: str | None = None) -> None:
        if self.client_id is None:
            suffix = uuid.uuid4().hex[:10]
            self.client_id = f"device-{self.device_id}-live-{suffix}" if self.device_id else f"live-{suffix}"
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
        )
        if self.username is not None:
            client.username_pw_set(self.username, self.password or "")
        ca = self.untrusted_ca or self.ca_file
        client.tls_set(ca_certs=ca, cert_reqs=ssl.CERT_REQUIRED)
        client.tls_insecure_set(False)
        if lwt_topic is not None:
            client.will_set(lwt_topic, payload=lwt_payload or "", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        client.reconnect_delay_set(min_delay=1, max_delay=8)
        self._client = client
        client.connect_async(self.host, self.port, 30)
        client.loop_start()

    def wait_connected(self, timeout: float = 12) -> bool:
        return self.connected.wait(timeout) and bool(self.connect_ok)

    def wait_subscribed(self, timeout: float = 8) -> bool:
        return self.subscribed.wait(timeout) and bool(self.subscribe_ok)

    def subscribe(self, topic: str, qos: int = 1) -> None:
        assert self._client is not None
        self.subscribed.clear()
        self.subscribe_ok = None
        self._client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> bool:
        assert self._client is not None
        info = self._client.publish(topic, payload=payload, qos=qos, retain=retain)
        try:
            info.wait_for_publish(timeout=8)
            return info.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception:
            return False

    def wait_message(self, matcher: Callable[[str, str], bool], timeout: float = 20) -> tuple[str, str] | None:
        from mqtt_live.http import wait_until

        found: list[tuple[str, str]] = []

        def _check() -> bool:
            for topic, payload in list(self.messages):
                if matcher(topic, payload):
                    found.append((topic, payload))
                    return True
            return False

        if wait_until(_check, timeout_seconds=timeout, interval=0.1):
            return found[0]
        return None

    def drop(self) -> None:
        """Close the socket without MQTT DISCONNECT so LWT can fire."""
        client = self._client
        if client is None:
            return
        sock = getattr(client, "_sock", None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        try:
            client.loop_stop()
        except Exception:
            pass
        self._client = None

    def close(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        self._client = None

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: object,
        _flags: object,
        reason_code: object,
        _properties: object = None,
    ) -> None:
        failed = bool(getattr(reason_code, "is_failure", False))
        self.connect_ok = not failed
        self.connect_count += 1
        if not failed:
            for topic in self.auto_subscribe:
                client.subscribe(topic, qos=1)
        self.connected.set()

    def _on_subscribe(
        self,
        _client: mqtt.Client,
        _userdata: object,
        _mid: int,
        reason_codes: object,
        _properties: object = None,
    ) -> None:
        codes = reason_codes if isinstance(reason_codes, list) else [reason_codes]
        self.subscribe_ok = not any(bool(getattr(code, "is_failure", False)) for code in codes)
        self.subscribed.set()

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: object,
        _flags: object,
        _reason_code: object,
        _properties: object = None,
    ) -> None:
        self.disconnect_event.set()

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        self.messages.append((message.topic, payload))
        client = self._client
        if self.handle_ping and self.device_id and client is not None:
            reply = ping_reply(payload)
            if reply is not None:
                # Do not wait_for_publish here: on_message runs on the paho loop thread.
                client.publish(
                    f"devices/{self.device_id}/commands/result",
                    json.dumps(reply),
                    qos=1,
                    retain=False,
                )


def try_connect(**kwargs: object) -> MqttSession:
    session = MqttSession(**kwargs)  # type: ignore[arg-type]
    session.start()
    session.wait_connected()
    return session
