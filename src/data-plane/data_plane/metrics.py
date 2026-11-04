"""Data-plane metrics: the only data-plane module that imports ``prometheus_client``.

Pull only: the process exposes ``GET /metrics`` and never connects to a metrics
system. With ``METRICS_ENABLED=false`` the endpoint is not mounted and recording
still works but is never read. Labels have fixed value sets; topics and device IDs
are never labels.
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

MQTT_CONNECTED = Gauge(
    "meteorcloud_dataplane_mqtt_connected", "1 when the platform MQTT client is connected to the broker"
)
# kind: status | metrics | command_result | other
MESSAGES_RECEIVED = Counter(
    "meteorcloud_dataplane_mqtt_messages_received_total", "Device MQTT messages received", ["kind"]
)
# result: ok | http_error | unreachable
INGEST_FORWARD = Counter(
    "meteorcloud_dataplane_ingest_forward_total", "Messages forwarded to the control-plane ingest API", ["result"]
)
# result: ok | error
PUBLISH = Counter("meteorcloud_dataplane_publish_total", "Publishes requested by the control plane", ["result"])
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration in seconds", ["method", "path"])

_SKIP_PATHS = frozenset({"/health", "/metrics"})


def message_kind(topic: str) -> str:
    parts = topic.split("/")
    if len(parts) >= 3 and parts[0] == "devices":
        suffix = "/".join(parts[2:])
        if suffix == "status":
            return "status"
        if suffix == "metrics":
            return "metrics"
        if suffix == "commands/result":
            return "command_result"
    return "other"


def _route_template(request: Request) -> str:
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", "<unmatched>")
    return "<unmatched>"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)
        path = _route_template(request)
        started = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            HTTP_REQUESTS.labels(method=request.method, path=path, status=status).inc()
            HTTP_DURATION.labels(method=request.method, path=path).observe(time.perf_counter() - started)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
