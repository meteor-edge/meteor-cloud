"""The metrics port: adapters, the on/off switch, and import boundaries."""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import metrics_backend
from app.adapters.noop_metrics import NoopMetrics
from app.adapters.prometheus_metrics import PrometheusMetrics
from app.core.config import Settings, get_settings
from app.main import create_app

APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def metrics_disabled(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("METRICS_ENABLED", "false")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("METRICS_ENABLED")
    get_settings.cache_clear()
    create_app()  # rebind the default (Prometheus) backend for later tests


def test_backend_selection() -> None:
    assert isinstance(metrics_backend(Settings(METRICS_ENABLED=True)), PrometheusMetrics)
    assert isinstance(metrics_backend(Settings(METRICS_ENABLED=False)), NoopMetrics)
    assert isinstance(metrics_backend(Settings(OBSERVABILITY_BACKEND="cloudwatch")), NoopMetrics)


def test_noop_adapter_accepts_every_call() -> None:
    noop = NoopMetrics()
    noop.counter("c_total", "c", ["a"]).labels(a="x").inc()
    gauge = noop.gauge("g", "g")
    gauge.inc()
    gauge.dec(2)
    gauge.set(3)
    noop.histogram("h", "h", ["a"], buckets=[0.1]).labels(a="x").observe(1.0)
    noop.register_gauge_callback("cb", "cb", ["a"], lambda: [({"a": "x"}, 1.0)])
    assert noop.exposition() is None
    assert noop.pull_endpoint is False


def test_metrics_disabled_hides_endpoint_and_requests_still_work(metrics_disabled: None) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/metrics").status_code == 404
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers.get("x-request-id")


def test_prometheus_callback_gauge_survives_failing_source() -> None:
    backend = PrometheusMetrics()

    def broken():
        raise RuntimeError("database down")

    backend.register_gauge_callback("meteorcloud_test_callback", "test", ["state"], broken)
    body, _ = backend.exposition()
    assert b"meteorcloud_test_callback" not in body

    backend.register_gauge_callback("meteorcloud_test_callback", "test", ["state"], lambda: [({"state": "ok"}, 2)])
    body, _ = backend.exposition()
    assert b'meteorcloud_test_callback{state="ok"} 2.0' in body


def test_only_the_prometheus_adapter_imports_prometheus_client() -> None:
    pattern = re.compile(r"^\s*(from|import)\s+prometheus_client\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if pattern.search(path.read_text()) and path.name != "prometheus_metrics.py"
    ]
    assert offenders == []
