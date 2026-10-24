"""Post-deployment health verification."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from http.client import HTTPException
from urllib import request
from urllib.parse import urlparse

from edge_installer.exceptions import HealthCheckError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthReport:
    infrastructure: str
    docker: str
    postgres: str
    redis: str
    backend: str
    frontend: str
    reverse_proxy: str
    # "disabled" when the installation has no bundled broker.
    mqtt: str = "disabled"

    def as_dict(self) -> dict[str, str]:
        return {
            "infrastructure": self.infrastructure,
            "docker": self.docker,
            "postgres": self.postgres,
            "redis": self.redis,
            "backend": self.backend,
            "frontend": self.frontend,
            "reverse_proxy": self.reverse_proxy,
            "mqtt": self.mqtt,
        }

    @property
    def is_healthy(self) -> bool:
        return all(value in ("healthy", "disabled") for value in self.as_dict().values())


def _http_check(url: str, *, timeout: int = 10) -> bool:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    # URLError, TLS and connection resets are all OSError.
    except (OSError, HTTPException):
        return False


def _tcp_check(host: str, port: int, *, timeout: int = 10) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe(base: str) -> tuple[bool, bool]:
    backend_ok = _http_check(f"{base}/api/v1/health") or _http_check(f"{base}/health")
    return backend_ok, _http_check(f"{base}/")


def verify_platform(
    url: str,
    *,
    timeout_seconds: int = 180,
    interval_seconds: float = 5,
    mqtt_port: int | None = None,
) -> HealthReport:
    # A new domain needs DNS and a Let's Encrypt certificate first, so retry until the deadline.
    # The broker is checked from outside too, so a missing security-group rule fails here.
    base = url.rstrip("/")
    host = urlparse(base).hostname or ""
    deadline = time.monotonic() + timeout_seconds

    def probe() -> tuple[bool, bool, bool]:
        backend, frontend = _probe(base)
        return backend, frontend, mqtt_port is None or _tcp_check(host, mqtt_port)

    backend_ok, frontend_ok, mqtt_ok = probe()
    while not (backend_ok and frontend_ok and mqtt_ok) and time.monotonic() < deadline:
        logger.info("Waiting for %s to become healthy", base)
        time.sleep(interval_seconds)
        backend_ok, frontend_ok, mqtt_ok = probe()
    proxy_ok = backend_ok and frontend_ok

    report = HealthReport(
        infrastructure="healthy",
        docker="healthy" if backend_ok else "unknown",
        postgres="healthy" if backend_ok else "unknown",
        redis="healthy" if backend_ok else "unknown",
        backend="healthy" if backend_ok else "unhealthy",
        frontend="healthy" if frontend_ok else "unhealthy",
        reverse_proxy="healthy" if proxy_ok else "unhealthy",
        mqtt="disabled" if mqtt_port is None else ("healthy" if mqtt_ok else "unhealthy"),
    )
    if not report.is_healthy:
        raise HealthCheckError(
            "One or more health checks failed.",
            stage="health_verification",
        )
    return report
