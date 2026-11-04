"""Metrics adapter used when metrics are switched off (``METRICS_ENABLED=false``)."""

from __future__ import annotations

from collections.abc import Sequence

from app.ports.observability import GaugeCallback


class _NoopHandle:
    def labels(self, **_labels: str) -> _NoopHandle:
        return self

    def inc(self, amount: float = 1.0) -> None:
        return None

    def dec(self, amount: float = 1.0) -> None:
        return None

    def set(self, value: float) -> None:
        return None

    def observe(self, value: float) -> None:
        return None


_HANDLE = _NoopHandle()


class NoopMetrics:
    """Accepts every call and records nothing; there is no exposition."""

    pull_endpoint = False

    def counter(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> _NoopHandle:
        return _HANDLE

    def gauge(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> _NoopHandle:
        return _HANDLE

    def histogram(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> _NoopHandle:
        return _HANDLE

    def register_gauge_callback(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str],
        callback: GaugeCallback,
    ) -> None:
        return None

    def exposition(self) -> None:
        return None
