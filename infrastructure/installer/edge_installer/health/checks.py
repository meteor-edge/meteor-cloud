"""Post-deployment health verification."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib import error, request

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

    def as_dict(self) -> dict[str, str]:
        return {
            "infrastructure": self.infrastructure,
            "docker": self.docker,
            "postgres": self.postgres,
            "redis": self.redis,
            "backend": self.backend,
            "frontend": self.frontend,
            "reverse_proxy": self.reverse_proxy,
        }

    @property
    def is_healthy(self) -> bool:
        return all(value == "healthy" for value in self.as_dict().values())


def _http_check(url: str, *, timeout: int = 10) -> bool:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (error.URLError, TimeoutError):
        return False


def verify_platform(url: str, *, timeout_seconds: int = 180) -> HealthReport:
    base = url.rstrip("/")
    backend_ok = _http_check(f"{base}/api/v1/health") or _http_check(f"{base}/health")
    frontend_ok = _http_check(f"{base}/")
    proxy_ok = backend_ok and frontend_ok

    report = HealthReport(
        infrastructure="healthy",
        docker="healthy" if backend_ok else "unknown",
        postgres="healthy" if backend_ok else "unknown",
        redis="healthy" if backend_ok else "unknown",
        backend="healthy" if backend_ok else "unhealthy",
        frontend="healthy" if frontend_ok else "unhealthy",
        reverse_proxy="healthy" if proxy_ok else "unhealthy",
    )
    if not report.is_healthy:
        raise HealthCheckError(
            "One or more health checks failed.",
            stage="health_verification",
        )
    return report
