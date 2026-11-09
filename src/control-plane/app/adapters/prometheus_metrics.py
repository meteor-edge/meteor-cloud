"""Prometheus metrics adapter: the only module that imports ``prometheus_client``.

prometheus_client keeps one process-wide registry, so instruments are created once per
name and reused when the app factory runs again (tests, reloads).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from app.ports.observability import GaugeCallback

__all__ = ["CONTENT_TYPE_LATEST", "PrometheusMetrics", "generate_latest"]

logger = logging.getLogger(__name__)


class _CallbackGaugeCollector(Collector):
    """Gauge family produced on scrape. The callback can be swapped without re-registering."""

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str], callback: GaugeCallback) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = list(labelnames)
        self.callback = callback

    def describe(self) -> list[Any]:
        # Returning nothing keeps registration from calling collect() (and the database) early.
        return []

    def collect(self) -> Iterator[GaugeMetricFamily]:
        try:
            samples = list(self.callback())
        except Exception:  # a scrape must never fail because one source is down
            logger.warning("metrics callback %s failed", self.name, exc_info=True)
            return
        if not samples:
            return
        family = GaugeMetricFamily(self.name, self.documentation, labels=self.labelnames)
        for labels, value in samples:
            family.add_metric([str(labels.get(key, "")) for key in self.labelnames], float(value))
        yield family


class PrometheusMetrics:
    """Instruments in the default prometheus_client registry, exposed on ``GET /metrics``."""

    pull_endpoint = True

    _instruments: dict[str, Any] = {}
    _callbacks: dict[str, _CallbackGaugeCollector] = {}

    def _get(self, kind: type, name: str, documentation: str, labelnames: Sequence[str], **kwargs: Any) -> Any:
        existing = self._instruments.get(name)
        if existing is not None:
            return existing
        instrument = kind(name, documentation, list(labelnames), **kwargs)
        self._instruments[name] = instrument
        return instrument

    def counter(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> Counter:
        return self._get(Counter, name, documentation, labelnames)

    def gauge(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> Gauge:
        return self._get(Gauge, name, documentation, labelnames)

    def histogram(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> Histogram:
        kwargs: dict[str, Any] = {"buckets": list(buckets)} if buckets else {}
        return self._get(Histogram, name, documentation, labelnames, **kwargs)

    def register_gauge_callback(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str],
        callback: GaugeCallback,
    ) -> None:
        existing = self._callbacks.get(name)
        if existing is not None:
            existing.callback = callback
            return
        collector = _CallbackGaugeCollector(name, documentation, labelnames, callback)
        REGISTRY.register(collector)
        self._callbacks[name] = collector

    def exposition(self) -> tuple[bytes, str]:
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
