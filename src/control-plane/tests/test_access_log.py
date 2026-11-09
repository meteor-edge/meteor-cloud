"""Structured per-request access log."""

from __future__ import annotations

import io
import json
import logging
import sys
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Generator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


def _app_output(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[TestClient, io.StringIO]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    application = create_app()

    @application.get("/__access/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @application.get("/__access_boom")
    def boom() -> None:
        raise RuntimeError("boom")

    return TestClient(application, raise_server_exceptions=False), buffer


def _access_lines(buffer: io.StringIO) -> list[dict]:
    lines = []
    for raw in buffer.getvalue().splitlines():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "http_request":
            lines.append(payload)
    return lines


@pytest.fixture(autouse=True)
def _clear_settings() -> Generator[None]:
    yield
    get_settings.cache_clear()


def test_default_follows_log_format() -> None:
    assert Settings(LOG_FORMAT="json").access_log_enabled is True
    assert Settings(LOG_FORMAT="console").access_log_enabled is False
    assert Settings(LOG_FORMAT="json", ACCESS_LOG="false").access_log_enabled is False
    assert Settings(LOG_FORMAT="console", ACCESS_LOG="true").access_log_enabled is True
    assert Settings(LOG_FORMAT="json", ACCESS_LOG="").access_log_enabled is True


def test_one_json_line_per_request_with_route_template(monkeypatch: pytest.MonkeyPatch) -> None:
    client, buffer = _app_output(monkeypatch, LOG_FORMAT="json")
    with client:
        response = client.get("/__access/abc?ticket=secret-ticket", headers={"X-Request-ID": "rid-1"})
    assert response.status_code == 200
    lines = _access_lines(buffer)
    assert len(lines) == 1
    line = lines[0]
    assert line["method"] == "GET"
    assert line["path"] == "/__access/{item_id}"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert line["request_id"] == "rid-1"
    assert line["level"] == "info"
    assert "secret-ticket" not in json.dumps(lines)


def test_health_and_metrics_are_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    client, buffer = _app_output(monkeypatch, LOG_FORMAT="json")
    with client:
        client.get("/health")
        client.get("/api/v1/health")
        client.get("/metrics")
    assert _access_lines(buffer) == []


def test_server_error_logged_at_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client, buffer = _app_output(monkeypatch, LOG_FORMAT="json")
    with client:
        assert client.get("/__access_boom").status_code == 500
    lines = _access_lines(buffer)
    assert [(line["status"], line["level"]) for line in lines] == [(500, "error")]


def test_access_log_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    client, buffer = _app_output(monkeypatch, LOG_FORMAT="json", ACCESS_LOG="false")
    with client:
        client.get("/__access/abc")
    assert _access_lines(buffer) == []


def test_access_log_works_with_metrics_off(monkeypatch: pytest.MonkeyPatch) -> None:
    client, buffer = _app_output(monkeypatch, LOG_FORMAT="json", METRICS_ENABLED="false")
    with client:
        client.get("/__access/abc")
    assert len(_access_lines(buffer)) == 1
    monkeypatch.delenv("METRICS_ENABLED")
    get_settings.cache_clear()
    create_app()
