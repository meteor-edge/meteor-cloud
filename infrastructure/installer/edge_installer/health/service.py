"""Health verification service."""

from __future__ import annotations

from edge_installer.health.checks import HealthReport, verify_platform


class HealthService:
    def verify(self, platform_url: str, *, timeout_seconds: int = 180) -> HealthReport:
        return verify_platform(platform_url, timeout_seconds=timeout_seconds)
