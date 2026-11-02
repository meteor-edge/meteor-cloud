"""Application metrics: declarations, HTTP timing middleware and the pull endpoint.

Every metric the control plane records is declared here, once. Call sites use the
declared handles (``HTTP_REQUESTS.labels(...).inc()``) and never import a metrics
library. ``configure_metrics`` binds the declarations to the adapter chosen from
settings (``app.adapters.metrics_backend``): Prometheus by default, a no-op when
``METRICS_ENABLED=false``.

Label rule: every label has a small fixed set of values. Never label by organization,
device, user, hostname, topic, IP address or free text.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from app.ports.observability import GaugeCallback, Metrics


class _Unbound:
    """Stand-in until the app factory binds a backend (records nothing)."""

    def labels(self, **_labels: str) -> _Unbound:
        return self

    def inc(self, amount: float = 1.0) -> None:
        return None

    def dec(self, amount: float = 1.0) -> None:
        return None

    def set(self, value: float) -> None:
        return None

    def observe(self, value: float) -> None:
        return None


_UNBOUND = _Unbound()
_backend: Metrics | None = None


class _Declared:
    """A metric declaration that forwards to the instrument of the active backend."""

    def __init__(
        self,
        kind: str,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> None:
        self.kind = kind
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self.buckets = tuple(buckets) if buckets else None
        self._handle: Any = None
        _DECLARED.append(self)

    def bind(self, backend: Metrics) -> None:
        if self.kind == "counter":
            self._handle = backend.counter(self.name, self.documentation, self.labelnames)
        elif self.kind == "gauge":
            self._handle = backend.gauge(self.name, self.documentation, self.labelnames)
        else:
            self._handle = backend.histogram(self.name, self.documentation, self.labelnames, self.buckets)

    def _current(self) -> Any:
        if self._handle is None:
            if _backend is None:
                return _UNBOUND
            self.bind(_backend)
        return self._handle

    def labels(self, **labels: str) -> Any:
        return self._current().labels(**labels)

    def inc(self, amount: float = 1.0) -> None:
        self._current().inc(amount)

    def dec(self, amount: float = 1.0) -> None:
        self._current().dec(amount)

    def set(self, value: float) -> None:
        self._current().set(value)

    def observe(self, value: float) -> None:
        self._current().observe(value)


_DECLARED: list[_Declared] = []


def counter(name: str, documentation: str, labelnames: Sequence[str] = ()) -> _Declared:
    return _Declared("counter", name, documentation, labelnames)


def gauge(name: str, documentation: str, labelnames: Sequence[str] = ()) -> _Declared:
    return _Declared("gauge", name, documentation, labelnames)


def histogram(
    name: str, documentation: str, labelnames: Sequence[str] = (), buckets: Sequence[float] | None = None
) -> _Declared:
    return _Declared("histogram", name, documentation, labelnames, buckets)


def configure_metrics(backend: Metrics) -> None:
    """Bind every declared metric to ``backend``. Called by the app factory."""
    global _backend
    _backend = backend
    for declared in _DECLARED:
        declared.bind(backend)


def register_gauge_callback(name: str, documentation: str, labelnames: Sequence[str], callback: GaugeCallback) -> None:
    if _backend is not None:
        _backend.register_gauge_callback(name, documentation, labelnames, callback)


def metrics_enabled() -> bool:
    """True when the bound backend serves a pull endpoint (``GET /metrics``)."""
    return _backend is not None and _backend.pull_endpoint


# --- Devices, enrollment and identity -----------------------------------------
# Every label below has a fixed set of values; see the comment on each metric.

# result: created | reregistered | invalid_token | conflict | error
DEVICE_REGISTRATIONS = counter(
    "meteorcloud_device_registrations_total", "Agent registration attempts by outcome", ["result"]
)
AGENT_HEARTBEATS = counter("meteorcloud_agent_heartbeats_total", "Agent heartbeats accepted")
# result: submitted | error
ENROLLMENT_REQUESTS = counter(
    "meteorcloud_enrollment_requests_total", "Enrollment requests submitted by agents", ["result"]
)
# status: pending | approved | rejected | expired | unauthorized | error
ENROLLMENT_POLLS = counter("meteorcloud_enrollment_polls_total", "Enrollment polls by returned status", ["status"])
# limiter: registration | enrollment_request | enrollment_poll | connectivity
RATE_LIMITED = counter("meteorcloud_rate_limited_total", "Requests rejected by a rate limiter", ["limiter"])
# result: success | failure
AUTH_LOGINS = counter("meteorcloud_auth_logins_total", "Console user logins by outcome", ["result"])

# --- HTTP (RED) --------------------------------------------------------------

HTTP_REQUESTS = counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_DURATION = histogram("http_request_duration_seconds", "HTTP request duration in seconds", ["method", "path"])

SKIP_PATHS = frozenset({"/health", "/api/v1/health", "/metrics"})


def _iter_routes(routes):
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_routes(nested.routes)
            continue
        yield route


def route_template(request: Request) -> str:
    path = request.url.path
    if path in SKIP_PATHS:
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
        path = route_template(request)
        if path in SKIP_PATHS:
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
    exposed = _backend.exposition() if _backend is not None else None
    if exposed is None:
        return Response(status_code=404)
    body, content_type = exposed
    return Response(body, media_type=content_type)
