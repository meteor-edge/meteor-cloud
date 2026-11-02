"""One structured log line per HTTP request.

Independent of metrics: it works with ``METRICS_ENABLED=false``. Fields are fixed so any
log pipeline can parse them: ``event="http_request"``, ``method``, ``path`` (the route
template, never the raw URL), ``status``, ``duration_ms`` and ``request_id`` (bound by
RequestIdMiddleware). Query strings, headers and client addresses are never logged.
"""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import SKIP_PATHS, route_template

logger = structlog.get_logger("app.access")


def _emit(method: str, path: str, status: int, started: float) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    log = logger.error if status >= 500 else logger.info
    log("http_request", method=method, path=path, status=status, duration_ms=duration_ms)


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = route_template(request)
        if path in SKIP_PATHS:
            return await call_next(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _emit(request.method, path, 500, started)
            raise
        _emit(request.method, path, response.status_code, started)
        return response
