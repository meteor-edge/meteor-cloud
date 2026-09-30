"""Prometheus metrics endpoint and HTTP timing."""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)

_SKIP_PATHS = frozenset({"/health", "/api/v1/health", "/metrics"})


def _iter_routes(routes):
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_routes(nested.routes)
            continue
        yield route


def _route_template(request: Request) -> str:
    path = request.url.path
    if path in _SKIP_PATHS:
        return path
    for route in _iter_routes(request.app.router.routes):
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            template = getattr(route, "path", None)
            if template:
                return template
    return "<unmatched>"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = _route_template(request)
        if path in _SKIP_PATHS:
            return await call_next(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            HTTP_REQUESTS.labels(method=request.method, path=path, status="500").inc()
            HTTP_DURATION.labels(method=request.method, path=path).observe(duration)
            raise
        duration = time.perf_counter() - started
        HTTP_REQUESTS.labels(method=request.method, path=path, status=str(response.status_code)).inc()
        HTTP_DURATION.labels(method=request.method, path=path).observe(duration)
        return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
