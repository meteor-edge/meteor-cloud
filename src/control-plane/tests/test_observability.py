"""Observability endpoint tests."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from tests.metrics_helpers import sample


def test_metrics_endpoint_exposes_prometheus(client: TestClient) -> None:
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "python_info" in body or "process_cpu_seconds_total" in body
    assert "http_requests_total" in body or "http_request_duration_seconds" in body


def test_health_sets_request_id_header(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-request-id")


def test_unhandled_exception_includes_request_id() -> None:
    application = create_app()

    @application.get("/__test_boom")
    def boom() -> None:
        raise RuntimeError("boom")

    before = sample("meteorcloud_unhandled_exceptions_total")
    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/__test_boom", headers={"X-Request-ID": "rid-test"})

    assert sample("meteorcloud_unhandled_exceptions_total") - before == 1
    assert response.status_code == 500
    assert response.headers.get("x-request-id") == "rid-test"
    assert response.json()["error"]["code"] == "internal_error"


def test_valid_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123_def.4"})
    assert response.headers["x-request-id"] == "abc-123_def.4"


@pytest.mark.parametrize(
    "header",
    ["x" * 65, "x" * 200, "has space", "line\tbreak", "semi;colon", 'quote"d'],
)
def test_unsafe_request_id_is_replaced(client: TestClient, header: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": header})
    returned = response.headers["x-request-id"]
    assert returned != header
    assert uuid.UUID(returned)


def test_overlong_request_id_does_not_break_audit(client: TestClient, db_session: Session) -> None:
    from app.audit.models import AuditEvent
    from tests.conftest import create_user

    create_user(db_session, email="owner@example.com")
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "strong-password"},
        headers={"X-Request-ID": "r" * 300},
    )
    assert response.status_code == 200, response.text
    request_ids = [event.request_id for event in db_session.query(AuditEvent).all()]
    assert request_ids
    assert all(rid is not None and len(rid) <= 64 for rid in request_ids)
    assert set(request_ids) == {response.headers["x-request-id"]}
