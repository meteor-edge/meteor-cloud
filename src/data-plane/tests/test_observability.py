"""Data-plane metrics, the METRICS_ENABLED switch and JSON logs."""

from __future__ import annotations

import io
import json
import logging
import sys
import urllib.error
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from data_plane.config import get_settings
from data_plane.ingest import ingest_message
from data_plane.logging_config import JsonFormatter, configure_logging
from data_plane.main import create_app
from data_plane.metrics import message_kind
from data_plane.runtime import set_mqtt_client

TOKEN = {"X-MQTT-Internal-Token": "dev-mqtt-internal"}


def _sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels or None) or 0.0


class _FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        if self.fail:
            raise RuntimeError("broker down")

    def watch_topic(self, topic: str) -> None:
        return None

    def unwatch_topic(self, topic: str) -> None:
        return None


def test_metrics_endpoint_lists_data_plane_metrics() -> None:
    set_mqtt_client(_FakeClient())
    with TestClient(create_app()) as client:
        client.post("/v1/publish", headers=TOKEN, json={"topic": "devices/x/commands", "payload": "{}"})
        body = client.get("/metrics").text
    for name in (
        "meteorcloud_dataplane_mqtt_connected",
        "meteorcloud_dataplane_publish_total",
        "http_requests_total",
    ):
        assert name in body


def test_publish_results_are_counted() -> None:
    ok_before = _sample("meteorcloud_dataplane_publish_total", result="ok")
    error_before = _sample("meteorcloud_dataplane_publish_total", result="error")
    body = {"topic": "devices/x/commands", "payload": "{}"}

    set_mqtt_client(_FakeClient())
    with TestClient(create_app()) as client:
        assert client.post("/v1/publish", headers=TOKEN, json=body).status_code == 200
    set_mqtt_client(_FakeClient(fail=True))
    with TestClient(create_app()) as client:
        assert client.post("/v1/publish", headers=TOKEN, json=body).status_code == 503

    assert _sample("meteorcloud_dataplane_publish_total", result="ok") - ok_before == 1
    assert _sample("meteorcloud_dataplane_publish_total", result="error") - error_before == 1


def test_metrics_disabled_hides_endpoint_but_publish_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METRICS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        set_mqtt_client(_FakeClient())
        with TestClient(create_app()) as client:
            assert client.get("/metrics").status_code == 404
            response = client.post("/v1/publish", headers=TOKEN, json={"topic": "t", "payload": "{}"})
            assert response.status_code == 200
            assert client.get("/health").status_code == 200
    finally:
        monkeypatch.delenv("METRICS_ENABLED")
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("raised", "result"),
    [
        (None, "ok"),
        (urllib.error.HTTPError("u", 500, "boom", {}, None), "http_error"),
        (urllib.error.URLError("refused"), "unreachable"),
    ],
)
def test_ingest_forward_results_are_counted(monkeypatch: pytest.MonkeyPatch, raised, result: str) -> None:
    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_urlopen(request, timeout=10):
        if raised is not None:
            raise raised
        return _Resp()

    monkeypatch.setattr("data_plane.ingest.urllib.request.urlopen", fake_urlopen)
    settings = MagicMock(control_plane_url="http://backend:8000", mqtt_internal_token="t")
    before = _sample("meteorcloud_dataplane_ingest_forward_total", result=result)
    ingest_message(settings, topic="devices/1/status", payload="{}")
    assert _sample("meteorcloud_dataplane_ingest_forward_total", result=result) - before == 1


def test_message_kind() -> None:
    assert message_kind("devices/abc/status") == "status"
    assert message_kind("devices/abc/metrics") == "metrics"
    assert message_kind("devices/abc/commands/result") == "command_result"
    assert message_kind("devices/abc/events") == "other"
    assert message_kind("random") == "other"


def test_json_formatter_emits_one_object_per_line(monkeypatch: pytest.MonkeyPatch) -> None:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    try:
        configure_logging("INFO", "json")
        logging.getLogger("data_plane.test").warning("hello %s", "world")
    finally:
        root.handlers.clear()
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(level)
    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert payload["event"] == "hello world"
    assert payload["level"] == "warning"
    assert payload["logger"] == "data_plane.test"
    assert isinstance(JsonFormatter(), logging.Formatter)
