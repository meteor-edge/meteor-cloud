"""HTTP MQTTGateway forwards publish/watch to the data-plane API."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from app.adapters.data_plane_mqtt import DataPlaneMQTTGateway
from app.core.config import Settings


def _gateway() -> DataPlaneMQTTGateway:
    return DataPlaneMQTTGateway(Settings(data_plane_url="http://data-plane:8081", mqtt_internal_token="secret"))


def test_publish_posts_json() -> None:
    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    with patch("app.adapters.data_plane_mqtt.urllib.request.urlopen", return_value=response) as urlopen:
        _gateway().publish("devices/1/commands", '{"type":"ping"}', qos=1, retain=False)
    request = urlopen.call_args[0][0]
    assert request.full_url == "http://data-plane:8081/v1/publish"
    assert request.get_header("X-mqtt-internal-token") == "secret"
    assert json.loads(request.data.decode()) == {
        "topic": "devices/1/commands",
        "payload": '{"type":"ping"}',
        "qos": 1,
        "retain": False,
    }


def test_watch_and_unwatch_paths() -> None:
    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    with patch("app.adapters.data_plane_mqtt.urllib.request.urlopen", return_value=response) as urlopen:
        gateway = _gateway()
        gateway.watch_topic("devices/+/events")
        gateway.unwatch_topic("devices/+/events")
    urls = [call[0][0].full_url for call in urlopen.call_args_list]
    assert urls == [
        "http://data-plane:8081/v1/subscriptions",
        "http://data-plane:8081/v1/subscriptions/unwatch",
    ]


def test_http_error_becomes_runtime_error() -> None:
    error = HTTPError("http://data-plane:8081/v1/publish", 503, "down", hdrs=None, fp=BytesIO())
    with patch("app.adapters.data_plane_mqtt.urllib.request.urlopen", side_effect=error):
        with pytest.raises(RuntimeError, match="HTTP 503"):
            _gateway().publish("t", "p")


def test_unreachable_becomes_runtime_error() -> None:
    with patch("app.adapters.data_plane_mqtt.urllib.request.urlopen", side_effect=URLError("offline")):
        with pytest.raises(RuntimeError, match="unreachable"):
            _gateway().publish("t", "p")
