"""Application factory wiring tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.metrics import MetricsMiddleware
from app.core.request_id import RequestIdMiddleware
from app.main import create_app


def test_metrics_route_is_hidden_from_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/metrics" not in schema["paths"]


def test_metrics_endpoint_is_registered_outside_schema(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200


def test_audit_router_is_mounted() -> None:
    application = create_app()

    assert "/api/v1/organizations/{organization_id}/audit-events" in application.openapi()["paths"]


def test_metrics_and_request_id_middleware_are_registered() -> None:
    application = create_app()

    middleware_classes = {middleware.cls for middleware in application.user_middleware}

    assert MetricsMiddleware in middleware_classes
    assert RequestIdMiddleware in middleware_classes
