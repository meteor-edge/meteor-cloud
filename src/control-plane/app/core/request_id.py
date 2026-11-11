"""Request ID context for logs and audit rows."""

from __future__ import annotations

import logging
import re
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REQUEST_ID_HEADER = "X-Request-ID"
# Client-supplied ids are kept only when short and plain, so they are safe as a log join key and fit
# audit_events.request_id (String(64)). Anything else gets a fresh UUID.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

logger = logging.getLogger(__name__)


def current_request_id() -> str | None:
    context = structlog.contextvars.get_contextvars()
    value = context.get("request_id")
    return str(value) if value else None


def resolve_request_id(header_value: str | None) -> str:
    """Return the client's request id when it is safe to reuse, otherwise a new UUID4."""
    if header_value and _VALID_REQUEST_ID.fullmatch(header_value):
        return header_value
    return str(uuid.uuid4())


def unhandled_error_response(request_id: str | None) -> JSONResponse:
    from app.core.metrics import UNHANDLED_EXCEPTIONS

    UNHANDLED_EXCEPTIONS.inc()
    logger.exception("unhandled error", extra={"request_id": request_id})
    headers = {REQUEST_ID_HEADER: request_id} if request_id else {}
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "Internal server error."}},
        headers=headers,
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
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
