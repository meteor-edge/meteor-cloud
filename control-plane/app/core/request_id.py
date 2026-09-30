"""Request ID context for logs and audit rows."""

from __future__ import annotations

import logging
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger(__name__)


def current_request_id() -> str | None:
    context = structlog.contextvars.get_contextvars()
    value = context.get("request_id")
    return str(value) if value else None


def unhandled_error_response(request_id: str | None) -> JSONResponse:
    logger.exception("unhandled error", extra={"request_id": request_id})
    headers = {REQUEST_ID_HEADER: request_id} if request_id else {}
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "Internal server error."}},
        headers=headers,
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        except Exception:
            response = unhandled_error_response(request_id)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
