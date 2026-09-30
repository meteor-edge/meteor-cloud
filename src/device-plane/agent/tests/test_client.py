"""Tests for the HTTP client error handling."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from edge_agent.client import AgentApiError, EdgeClient


def _http_error(status: int, body: dict) -> urllib.error.HTTPError:
    fp = io.BytesIO(json.dumps(body).encode("utf-8"))
    return urllib.error.HTTPError(
        url="http://localhost/api",
        code=status,
        msg="error",
        hdrs=None,
        fp=fp,
    )


def test_register_success(monkeypatch) -> None:
    client = EdgeClient("http://localhost:8000")

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    payload = {"device_id": "d1", "device_token": "dev_x", "organization_id": "o1", "name": "n"}

    def fake_urlopen(request, timeout):  # noqa: ARG001
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = client.register(token="reg_x", inventory={"mac_addresses": ["aa:bb:cc:dd:ee:ff"]}, name="n")
    assert result["device_token"] == "dev_x"


def test_http_error_is_mapped(monkeypatch) -> None:
    client = EdgeClient("http://localhost:8000")

    def fake_urlopen(request, timeout):  # noqa: ARG001
        raise _http_error(401, {"error": {"code": "invalid_registration_token", "message": "bad"}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(AgentApiError) as excinfo:
        client.register(token="reg_x", inventory={}, name=None)
    assert excinfo.value.status == 401
    assert excinfo.value.code == "invalid_registration_token"


def test_network_error_is_mapped(monkeypatch) -> None:
    client = EdgeClient("http://localhost:8000")

    def fake_urlopen(request, timeout):  # noqa: ARG001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(AgentApiError) as excinfo:
        client.heartbeat(device_token="dev_x", payload={})
    assert excinfo.value.code == "network_error"


def test_health_success(monkeypatch) -> None:
    client = EdgeClient("http://localhost:8000")

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):  # noqa: ARG001
        assert request.get_method() == "GET"
        return FakeResponse(json.dumps({"status": "ok"}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert client.health()["status"] == "ok"
