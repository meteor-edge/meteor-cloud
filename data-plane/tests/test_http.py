"""HTTP token and publish/watch forwarding tests (MQTT client is faked)."""

from __future__ import annotations

import os

os.environ["MQTT_ENABLED"] = "false"

from fastapi.testclient import TestClient

from data_plane.main import create_app
from data_plane.runtime import set_mqtt_client

TOKEN = {"X-MQTT-Internal-Token": "dev-mqtt-internal"}


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []
        self.watched: list[str] = []
        self.unwatched: list[str] = []

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        self.published.append((topic, payload, qos, retain))

    def watch_topic(self, topic: str) -> None:
        self.watched.append(topic)

    def unwatch_topic(self, topic: str) -> None:
        self.unwatched.append(topic)


def _client(fake: FakeClient | None = None) -> TestClient:
    application = create_app()
    set_mqtt_client(fake)
    return TestClient(application)


def test_health() -> None:
    with _client() as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_publish_requires_token() -> None:
    fake = FakeClient()
    with _client(fake) as client:
        response = client.post("/v1/publish", json={"topic": "t", "payload": "p"})
    assert response.status_code == 401
    assert fake.published == []


def test_publish_forwards() -> None:
    fake = FakeClient()
    with _client(fake) as client:
        response = client.post(
            "/v1/publish",
            headers=TOKEN,
            json={"topic": "devices/1/commands", "payload": "{}", "qos": 1, "retain": False},
        )
    assert response.status_code == 200, response.text
    assert fake.published == [("devices/1/commands", "{}", 1, False)]


def test_watch_and_unwatch() -> None:
    fake = FakeClient()
    with _client(fake) as client:
        watch = client.post("/v1/subscriptions", headers=TOKEN, json={"topic": "lab/temp"})
        unwatch = client.post("/v1/subscriptions/unwatch", headers=TOKEN, json={"topic": "lab/temp"})
    assert watch.status_code == 200
    assert unwatch.status_code == 200
    assert fake.watched == ["lab/temp"]
    assert fake.unwatched == ["lab/temp"]


def test_publish_without_client_is_unavailable() -> None:
    with _client(None) as client:
        response = client.post("/v1/publish", headers=TOKEN, json={"topic": "t", "payload": "p"})
    assert response.status_code == 503
