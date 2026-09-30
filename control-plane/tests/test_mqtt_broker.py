"""Unit tests for platform MQTT client subscribe accounting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt

from data_plane.connectivity.mqtt.broker import PlatformMqttClient


class _FakeBroker:
    def __init__(self, *, connected: bool = True, result: int = mqtt.MQTT_ERR_SUCCESS) -> None:
        self._connected = connected
        self._result = result
        self.subscribes: list[tuple[str, int]] = []

    def is_connected(self) -> bool:
        return self._connected

    def subscribe(self, topic: str, qos: int = 1) -> tuple[int, int]:
        self.subscribes.append((topic, qos))
        return self._result, 1


def _platform() -> PlatformMqttClient:
    return PlatformMqttClient(settings=MagicMock())


def test_subscribe_failure_still_marks_ready_when_pending_reaches_zero() -> None:
    client = _platform()
    client._pending_subs = 1
    client._on_subscribe(None, None, 1, [SimpleNamespace(is_failure=True)])
    assert client._pending_subs == 0
    assert client._ready.is_set()


def test_watch_topic_while_connected_increments_pending_subs() -> None:
    client = _platform()
    broker = _FakeBroker(connected=True)
    client._client = broker
    client._ready.set()

    client.watch_topic("lab/temp")

    assert broker.subscribes == [("lab/temp", 1)]
    assert client._pending_subs == 1
    client._on_subscribe(None, None, 1, [0])
    assert client._pending_subs == 0
    assert client._ready.is_set()


def test_watch_topic_subscribe_error_releases_pending() -> None:
    client = _platform()
    broker = _FakeBroker(connected=True, result=mqtt.MQTT_ERR_NO_CONN)
    client._client = broker
    client._ready.set()

    client.watch_topic("lab/temp")

    assert client._pending_subs == 0
    assert client._ready.is_set()


def test_watch_topic_before_connect_does_not_increment_pending() -> None:
    client = _platform()
    broker = _FakeBroker(connected=False)
    client._client = broker

    client.watch_topic("lab/temp")

    assert broker.subscribes == [("lab/temp", 1)]
    assert client._pending_subs == 0
    assert not client._ready.is_set()
