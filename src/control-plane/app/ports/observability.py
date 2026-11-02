"""Metrics port.

The application declares its metrics once (``app.core.metrics``) and records through
these small handles. The active adapter decides where the numbers go:

- ``app.adapters.prometheus_metrics`` — pull exposition on ``GET /metrics`` (default)
- ``app.adapters.noop_metrics`` — ``METRICS_ENABLED=false``; every call is free

The shape maps 1:1 onto Prometheus and OpenTelemetry instruments, so another backend
(OTel, CloudWatch, StatsD, ...) is a new adapter, not a change to call sites.
The application never pushes to or waits for a metrics system.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Protocol

# One sample of a gauge computed on read: (label values by name, value).
GaugeSample = tuple[Mapping[str, str], float]
GaugeCallback = Callable[[], Iterable[GaugeSample]]


class CounterHandle(Protocol):
    def labels(self, **labels: str) -> CounterHandle: ...

    def inc(self, amount: float = 1.0) -> None: ...


class GaugeHandle(Protocol):
    def labels(self, **labels: str) -> GaugeHandle: ...

    def inc(self, amount: float = 1.0) -> None: ...

    def dec(self, amount: float = 1.0) -> None: ...

    def set(self, value: float) -> None: ...


class HistogramHandle(Protocol):
    def labels(self, **labels: str) -> HistogramHandle: ...

    def observe(self, value: float) -> None: ...


class Metrics(Protocol):
    """Factory for instruments plus an optional pull exposition."""

    # True when exposition() serves a scrape endpoint; GET /metrics is mounted only then.
    pull_endpoint: bool

    def counter(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> CounterHandle: ...

    def gauge(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> GaugeHandle: ...

    def histogram(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> HistogramHandle: ...

    def register_gauge_callback(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str],
        callback: GaugeCallback,
    ) -> None:
        """Register a gauge whose samples are produced by ``callback`` when metrics are read."""
        ...

    def exposition(self) -> tuple[bytes, str] | None:
        """Return ``(body, content_type)`` for a pull endpoint, or None when the backend has none."""
        ...
