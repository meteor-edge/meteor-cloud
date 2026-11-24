"""Offline tests for the sink: SQLite store and a stand-in for the SDK client.

    pip install meteorcloud pytest && python -m pytest -q test_sink.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import sink

from meteorcloud import Event

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def event(i: int, at: datetime, stream: str = "energy.metrics") -> Event:
    return Event(id=f"e{i}", stream=stream, kind="telemetry", type=stream, data={"power": i}, time=at,
                 received_at=at, organization_id="org-1", application="meter", device_id="dev-1", seq=i)


class FakeStreams:
    def __init__(self, history: list[Event]) -> None:
        self.history = history
        self.queries: list[tuple[str, datetime, datetime]] = []

    def query_all(self, stream: str, *, start: datetime, end: datetime, page_size: int = 500):
        self.queries.append((stream, start, end))
        return [e for e in self.history if e.stream == stream and start <= e.time < end]


class FakeClient:
    def __init__(self, history: list[Event]) -> None:
        self.telemetry = FakeStreams(history)


def settings(tmp_path, **overrides) -> sink.Settings:
    env = {"SINK_DATABASE_URL": f"sqlite:///{tmp_path / 'events.db'}", "SINK_STREAMS": "energy.metrics",
           "SINK_SETTLE": "0s", "SINK_OVERLAP": "10m", **overrides}
    return sink.Settings.from_env(env)


def make(tmp_path, history: list[Event]) -> tuple[sink.Sink, FakeClient]:
    s = settings(tmp_path)
    client = FakeClient(history)
    sk = sink.Sink(s, client, sink.open_store(s.database_url), clock=lambda: NOW)  # type: ignore[arg-type]
    return sk, client


def rows(tmp_path) -> list[str]:
    with sqlite3.connect(tmp_path / "events.db") as conn:
        return [r[0] for r in conn.execute("SELECT event_id FROM meteor_events ORDER BY event_id")]


def test_duration_parsing() -> None:
    assert sink.parse_duration("90") == timedelta(seconds=90)
    assert sink.parse_duration("5m") == timedelta(minutes=5)
    assert sink.parse_duration("1d") == timedelta(days=1)
    with pytest.raises(ValueError):
        sink.parse_duration("soon")


def test_sweep_backfills_and_moves_checkpoint(tmp_path) -> None:
    history = [event(i, NOW - timedelta(minutes=30 - i)) for i in range(5)]
    sk, client = make(tmp_path, history)
    sk.with_db("init", lambda: sk.store.add_stream("telemetry", "energy.metrics", NOW - timedelta(hours=1)))
    assert sk.sweep_once() == 5
    assert rows(tmp_path) == ["e0", "e1", "e2", "e3", "e4"]
    assert sk.store.checkpoints() == {("telemetry", "energy.metrics"): NOW}
    # The next sweep starts one overlap before the checkpoint.
    assert sk.sweep_once() == 0
    assert client.telemetry.queries[-1][1] == NOW - timedelta(minutes=10)


def test_live_and_sweep_store_each_event_once(tmp_path) -> None:
    history = [event(i, NOW - timedelta(minutes=5)) for i in range(3)]
    sk, _ = make(tmp_path, history)
    sk.with_db("init", lambda: sk.store.add_stream("telemetry", "energy.metrics", NOW - timedelta(minutes=8)))
    assert sk.with_db("write", lambda: sk.store.write(history[:2])) == 2  # arrived live
    assert sk.sweep_once() == 1  # only the one the live path missed
    assert rows(tmp_path) == ["e0", "e1", "e2"]


def test_pattern_subscription_discovers_streams(tmp_path) -> None:
    sk, _ = make(tmp_path, [])
    sk.track_streams([event(1, NOW, stream="energy.phases")])
    assert ("telemetry", "energy.phases") in sk.store.checkpoints()


def test_full_live_queue_drops_instead_of_blocking(tmp_path) -> None:
    sk = sink.Sink(settings(tmp_path, SINK_QUEUE_SIZE="1"), FakeClient([]), sink.SQLiteStore("sqlite:///:memory:"))
    sk.on_event(event(1, NOW))
    sk.on_event(event(2, NOW))
    assert sk.dropped == 1
