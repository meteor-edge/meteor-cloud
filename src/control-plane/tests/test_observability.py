"""Observability endpoint tests."""

from fastapi.testclient import TestClient

from app.main import create_app


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

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/__test_boom", headers={"X-Request-ID": "rid-test"})

    assert response.status_code == 500
    assert response.headers.get("x-request-id") == "rid-test"
    assert response.json()["error"]["code"] == "internal_error"
