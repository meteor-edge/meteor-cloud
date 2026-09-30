"""Request ID middleware and helper tests."""

from __future__ import annotations

import json
import uuid

import structlog
from fastapi.testclient import TestClient

from app.core.request_id import REQUEST_ID_HEADER, current_request_id, unhandled_error_response
from app.main import create_app


def test_request_id_header_is_generated_when_missing(client: TestClient) -> None:
    response = client.get("/health")

    request_id = response.headers.get(REQUEST_ID_HEADER.lower())
    assert request_id
    # Generated IDs are UUID4 strings.
    assert uuid.UUID(request_id).version == 4


def test_request_id_header_is_echoed_back_when_provided(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "caller-supplied-id"})

    assert response.headers.get(REQUEST_ID_HEADER.lower()) == "caller-supplied-id"


def test_request_id_differs_between_requests(client: TestClient) -> None:
    first = client.get("/health").headers.get(REQUEST_ID_HEADER.lower())
    second = client.get("/health").headers.get(REQUEST_ID_HEADER.lower())

    assert first != second


def test_current_request_id_returns_none_outside_request_context() -> None:
    structlog.contextvars.clear_contextvars()

    assert current_request_id() is None


def test_current_request_id_reads_bound_context() -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="bound-id")
    try:
        assert current_request_id() == "bound-id"
    finally:
        structlog.contextvars.clear_contextvars()


def test_unhandled_error_response_without_request_id_omits_header() -> None:
    response = unhandled_error_response(None)

    assert response.status_code == 500
    assert REQUEST_ID_HEADER not in response.headers
    body = json.loads(response.body)
    assert body["error"]["code"] == "internal_error"


def test_unhandled_error_response_with_request_id_sets_header() -> None:
    response = unhandled_error_response("rid-abc-123")

    assert response.headers[REQUEST_ID_HEADER] == "rid-abc-123"


def test_unhandled_exception_response_includes_caller_supplied_request_id() -> None:
    application = create_app()

    @application.get("/api/v1/__test_request_id_boom")
    def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/api/v1/__test_request_id_boom",
            headers={REQUEST_ID_HEADER: "rid-test"},
        )

    assert response.status_code == 500
    assert response.headers.get(REQUEST_ID_HEADER.lower()) == "rid-test"
    assert response.json()["error"]["code"] == "internal_error"
