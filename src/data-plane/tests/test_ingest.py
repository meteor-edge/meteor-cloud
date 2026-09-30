from unittest.mock import MagicMock

from data_plane.ingest import ingest_message


def test_ingest_posts_json(monkeypatch) -> None:
    captured: dict = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_urlopen(request, timeout=10):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["token"] = request.get_header("X-mqtt-internal-token")
        return _Resp()

    monkeypatch.setattr("data_plane.ingest.urllib.request.urlopen", fake_urlopen)
    settings = MagicMock()
    settings.control_plane_url = "http://backend:8000"
    settings.mqtt_internal_token = "secret"
    ingest_message(settings, topic="devices/1/status", payload="{}")
    assert captured["url"] == "http://backend:8000/internal/mqtt/ingest"
    assert captured["token"] == "secret"
    assert b'"topic"' in captured["body"]
