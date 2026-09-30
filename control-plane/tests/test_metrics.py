"""Prometheus metrics middleware and endpoint tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.core.metrics import CONTENT_TYPE_LATEST, generate_latest
from app.main import create_app


def _metrics_text() -> str:
    return generate_latest().decode("utf-8")


def _has_sample(body: str, metric: str, **labels: str) -> bool:
    """Return True if a metric line for ``metric`` carries all given labels."""
    prefix = f"{metric}{{"
    for line in body.splitlines():
        if not line.startswith(prefix):
            continue
        if all(f'{key}="{value}"' in line for key, value in labels.items()):
            return True
    return False


def test_metrics_endpoint_returns_prometheus_content_type(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST


def test_metrics_skips_health_and_metrics_paths(client: TestClient) -> None:
    client.get("/health")
    client.get("/api/v1/health")
    client.get("/metrics")

    body = _metrics_text()
    assert not _has_sample(body, "http_requests_total", path="/health")
    assert not _has_sample(body, "http_requests_total", path="/api/v1/health")
    assert not _has_sample(body, "http_requests_total", path="/metrics")


def test_metrics_records_route_template_not_raw_path(client: TestClient) -> None:
    random_id = str(uuid.uuid4())
    client.get(f"/api/v1/organizations/{random_id}")

    body = _metrics_text()
    assert _has_sample(
        body,
        "http_requests_total",
        method="GET",
        path="/api/v1/organizations/{organization_id}",
    )
    assert random_id not in body


def test_metrics_records_unmatched_label_for_unknown_route(client: TestClient) -> None:
    response = client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    body = _metrics_text()
    assert _has_sample(body, "http_requests_total", path="<unmatched>")


def test_metrics_counts_500_status_on_unhandled_exception() -> None:
    application = create_app()

    @application.get("/api/v1/__test_metrics_boom")
    def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/v1/__test_metrics_boom")

    assert response.status_code == 500
    body = _metrics_text()
    assert _has_sample(
        body,
        "http_requests_total",
        method="GET",
        path="/api/v1/__test_metrics_boom",
        status="500",
    )
    assert _has_sample(
        body,
        "http_request_duration_seconds_count",
        method="GET",
        path="/api/v1/__test_metrics_boom",
    )


def test_metrics_records_actual_status_code_for_handled_errors(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong"},
    )
    assert response.status_code == 401

    body = _metrics_text()
    assert _has_sample(
        body,
        "http_requests_total",
        method="POST",
        path="/api/v1/auth/login",
        status="401",
    )
