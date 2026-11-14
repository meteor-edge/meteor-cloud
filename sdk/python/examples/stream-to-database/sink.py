"""Stream MeteorCloud data into a database you own.

This is a plain SDK application. It runs wherever you like (your laptop, a VM,
a container next to your database) and needs only an application token.

How it keeps your copy complete:

* A live subscription delivers new events within milliseconds.
* A sweep reads history with ``query`` every ``SINK_SWEEP_INTERVAL`` and writes
  whatever the live path missed: events published while this process was down,
  while the live connection was reconnecting, or while the database was slow.
  The sweep stores how far it got (``complete_through``) in your database, in
  the same transaction as the events, so a restart continues where it stopped.
* Every write is ``INSERT ... ON CONFLICT DO NOTHING`` on the event id, so an
  event that arrives both live and through the sweep is stored once.

Configuration is environment variables only; see README.md.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import random
import re
import signal
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from meteorcloud import Event, MeteorClient, MeteorCloudError

log = logging.getLogger("sink")

KINDS = {"telemetry": "telemetry", "events": "event", "inference": "inference"}


# ---- configuration ---------------------------------------------------------------

def parse_duration(text: str) -> timedelta:
    """``30s``, ``5m``, ``2h``, ``1d`` or a number of seconds."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", text)
    if not match:
        raise ValueError(f"not a duration: {text!r} (use for example 30s, 5m, 2h, 1d)")
    value, unit = float(match.group(1)), match.group(2) or "s"
    return timedelta(seconds=value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])


@dataclass(frozen=True)
class Settings:
    database_url: str
    streams: list[str]
    kinds: list[str]
    backfill: timedelta
    sweep_interval: timedelta
    overlap: timedelta
    settle: timedelta
    batch_size: int
    flush_interval: float
    queue_size: int

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        url = env.get("SINK_DATABASE_URL", "").strip()
        if not url:
            raise SystemExit("SINK_DATABASE_URL is required, e.g. postgresql://user:pass@localhost:5432/mydb")
        streams = [s.strip() for s in env.get("SINK_STREAMS", "").split(",") if s.strip()]
        if not streams:
            raise SystemExit("SINK_STREAMS is required, e.g. energy.metrics or energy.*")
        kinds = [k.strip() for k in env.get("SINK_KINDS", "telemetry").split(",") if k.strip()]
        unknown = [k for k in kinds if k not in KINDS]
        if unknown:
            raise SystemExit(f"SINK_KINDS: unknown {unknown}; use telemetry, events, inference")
        return cls(
            database_url=url,
            streams=streams,
            kinds=kinds,
            backfill=parse_duration(env.get("SINK_BACKFILL", "1h")),
            sweep_interval=parse_duration(env.get("SINK_SWEEP_INTERVAL", "60s")),
            overlap=parse_duration(env.get("SINK_OVERLAP", "10m")),
            settle=parse_duration(env.get("SINK_SETTLE", "5s")),
            batch_size=int(env.get("SINK_BATCH_SIZE", "500")),
            flush_interval=float(env.get("SINK_FLUSH_INTERVAL", "1")),
            queue_size=int(env.get("SINK_QUEUE_SIZE", "10000")),
        )


def is_pattern(stream: str) -> bool:
    return "*" in stream


# ---- your database -----------------------------------------------------------------

class Store:
    """Writes events and sweep checkpoints. Subclasses supply the SQL dialect."""

    ph = "%s"  # parameter placeholder

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def _ddl(self) -> list[str]: ...

    def __init__(self) -> None:
        self.conn: Any = None

    def ensure_schema(self) -> None:
        cur = self.conn.cursor()
        for statement in self._ddl():
            cur.execute(statement)
        self.conn.commit()

    def _row(self, e: Event) -> tuple[Any, ...]:
        return (e.organization_id, e.id, e.kind, e.stream, e.type, e.time, e.received_at, e.application,
                e.device_id, e.environment, e.seq, json.dumps(e.data, separators=(",", ":")))

    def write(self, events: list[Event], checkpoint: tuple[str, str, datetime] | None = None) -> int:
        """Insert events (duplicates are skipped) and optionally move one checkpoint, atomically.

        Returns how many events were new.
        """
        p = self.ph
        cur = self.conn.cursor()
        inserted = 0
        try:
            for e in events:
                cur.execute(
                    "INSERT INTO meteor_events (organization_id, event_id, kind, stream, type, time, received_at,"
                    " application, device_id, environment, seq, data)"
                    f" VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {self._json(p)})"
                    " ON CONFLICT (organization_id, event_id) DO NOTHING",
                    self._row(e),
                )
                inserted += max(cur.rowcount, 0)
            if checkpoint is not None:
                kind, stream, through = checkpoint
                cur.execute(
                    "INSERT INTO meteor_sink_checkpoints (kind, stream, complete_through)"
                    f" VALUES ({p}, {p}, {p}) ON CONFLICT (kind, stream) DO UPDATE"
                    " SET complete_through = excluded.complete_through",
                    (kind, stream, self._ts(through)),
                )
            self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except Exception:  # noqa: BLE001 - the connection may be gone; reconnect handles it
                pass
            raise
        return inserted

    def add_stream(self, kind: str, stream: str, through: datetime) -> None:
        """Start sweeping a stream from ``through`` unless it is already tracked."""
        p = self.ph
        cur = self.conn.cursor()
        cur.execute(
            f"INSERT INTO meteor_sink_checkpoints (kind, stream, complete_through) VALUES ({p}, {p}, {p})"
            " ON CONFLICT (kind, stream) DO NOTHING",
            (kind, stream, self._ts(through)),
        )
        self.conn.commit()

    def checkpoints(self) -> dict[tuple[str, str], datetime]:
        cur = self.conn.cursor()
        cur.execute("SELECT kind, stream, complete_through FROM meteor_sink_checkpoints")
        return {(k, s): self._parse_ts(t) for k, s, t in cur.fetchall()}

    def _json(self, p: str) -> str:
        return p

    def _ts(self, value: datetime) -> Any:
        return value

    def _parse_ts(self, value: Any) -> datetime:
        return value


class PostgresStore(Store):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = urlparse(url)

    def connect(self) -> None:
        import pg8000.dbapi  # BSD-3-Clause, pure Python

        u = self.url
        self.conn = pg8000.dbapi.connect(
            user=unquote(u.username or "postgres"), password=unquote(u.password) if u.password else None,
            host=u.hostname or "localhost", port=u.port or 5432, database=(u.path or "/postgres")[1:],
            timeout=30,
        )

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None

    def _json(self, p: str) -> str:
        return f"CAST({p} AS jsonb)"

    def _ddl(self) -> list[str]:
        return [
            """CREATE TABLE IF NOT EXISTS meteor_events (
                organization_id text        NOT NULL,
                event_id        text        NOT NULL,
                kind            text        NOT NULL,
                stream          text        NOT NULL,
                type            text        NOT NULL,
                time            timestamptz NOT NULL,
                received_at     timestamptz,
                application     text,
                device_id       text,
                environment     text,
                seq             bigint,
                data            jsonb       NOT NULL,
                stored_at       timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (organization_id, event_id))""",
            "CREATE INDEX IF NOT EXISTS meteor_events_stream_time ON meteor_events (stream, time)",
            """CREATE TABLE IF NOT EXISTS meteor_sink_checkpoints (
                kind             text        NOT NULL,
                stream           text        NOT NULL,
                complete_through timestamptz NOT NULL,
                PRIMARY KEY (kind, stream))""",
        ]


class SQLiteStore(Store):
    ph = "?"

    def __init__(self, url: str) -> None:
        super().__init__()
        self.path = url.split("sqlite:///", 1)[1] or "events.db"

    def connect(self) -> None:
        # The writer and the sweeper share this connection; Sink.db_lock serializes them.
        self.conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _row(self, e: Event) -> tuple[Any, ...]:
        row = list(super()._row(e))
        row[5] = self._ts(e.time)
        row[6] = self._ts(e.received_at) if e.received_at else None
        return tuple(row)

    def _ts(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def _parse_ts(self, value: Any) -> datetime:
        return datetime.fromisoformat(value)

    def _ddl(self) -> list[str]:
        return [
            """CREATE TABLE IF NOT EXISTS meteor_events (
                organization_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL, stream TEXT NOT NULL,
                type TEXT NOT NULL, time TEXT NOT NULL, received_at TEXT, application TEXT, device_id TEXT,
                environment TEXT, seq INTEGER, data TEXT NOT NULL,
                stored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (organization_id, event_id))""",
            "CREATE INDEX IF NOT EXISTS meteor_events_stream_time ON meteor_events (stream, time)",
            """CREATE TABLE IF NOT EXISTS meteor_sink_checkpoints (
                kind TEXT NOT NULL, stream TEXT NOT NULL, complete_through TEXT NOT NULL,
                PRIMARY KEY (kind, stream))""",
        ]


def open_store(url: str) -> Store:
    if url.startswith(("postgresql://", "postgres://")):
        return PostgresStore(url)
    if url.startswith("sqlite:///"):
        return SQLiteStore(url)
    raise SystemExit("SINK_DATABASE_URL must start with postgresql:// or sqlite:///")


# ---- retries ---------------------------------------------------------------------

class Backoff:
    """Exponential backoff with jitter: 0.5s, 1s, 2s ... capped at 30s."""

    def __init__(self, base: float = 0.5, cap: float = 30.0) -> None:
        self.base, self.cap, self.attempt = base, cap, 0

    def next(self) -> float:
        delay = min(self.cap, self.base * 2 ** self.attempt) * random.uniform(0.5, 1.0)
        self.attempt += 1
        return delay

    def reset(self) -> None:
        self.attempt = 0


# ---- the sink --------------------------------------------------------------------

class Sink:
    def __init__(self, settings: Settings, client: MeteorClient, store: Store,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.s = settings
        self.client = client
        self.store = store
        self.clock = clock
        self.live: queue.Queue[Event] = queue.Queue(maxsize=settings.queue_size)
        self.stop = threading.Event()
        self.db_lock = threading.Lock()  # one connection, used by the writer and the sweeper
        self.dropped = 0
        self.stats = {"live": 0, "swept": 0}
        self.known: set[tuple[str, str]] = set()  # (kind, stream) pairs the sweep covers

    # -- database access with reconnect + retry --

    def with_db(self, what: str, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` against the database, reconnecting and retrying with backoff.

        Retries until it works. Once shutdown has started it tries once more and then
        raises, so a database that stays away cannot hold the process up.
        """
        backoff = Backoff()
        while True:
            try:
                with self.db_lock:
                    if self.store.conn is None:
                        self.store.connect()
                        self.store.ensure_schema()
                    return fn()
            except Exception as err:  # noqa: BLE001 - any driver error: reconnect and retry
                with self.db_lock:
                    self.store.close()
                if self.stop.is_set():
                    raise
                delay = backoff.next()
                log.warning("database %s failed (%s: %s), retrying in %.1fs", what, type(err).__name__, err, delay)
                self.stop.wait(delay)

    # -- live path --

    def on_event(self, event: Event) -> None:
        # Runs on the SDK's network thread: never block it, or the server drops the
        # connection as a slow consumer. A full queue drops the event here; the next
        # sweep writes it.
        try:
            self.live.put_nowait(event)
        except queue.Full:
            self.dropped += 1
            if self.dropped % 1000 == 1:
                log.warning("live queue full, the sweep will catch up (dropped %d so far)", self.dropped)

    def writer(self) -> None:
        while True:
            stopping = self.stop.is_set()
            batch: list[Event] = []
            deadline = time.monotonic() + self.s.flush_interval
            while len(batch) < self.s.batch_size:
                try:
                    batch.append(self.live.get(timeout=0 if stopping else max(0.0, deadline - time.monotonic())))
                except queue.Empty:
                    break
            if not batch:
                if stopping:
                    return
                continue
            try:
                new = self.with_db("write", lambda b=batch: self.store.write(b))
                self.track_streams(batch)
            except Exception as err:  # noqa: BLE001 - only raised once shutdown started
                lost = len(batch) + self.live.qsize()
                log.warning("not written at shutdown: %d live events (%s). The sweep stores them after the "
                            "next start, because the checkpoint only moves after a sweep wrote its window.",
                            lost, type(err).__name__)
                return
            self.stats["live"] += new
            log.debug("live: wrote %d new of %d", new, len(batch))

    def track_streams(self, events: Iterable[Event]) -> None:
        """A pattern subscription reveals concrete stream names; start sweeping them too."""
        seen = {(e.kind, e.stream): e.time for e in events}
        for (kind, stream), first in seen.items():
            if (kind, stream) not in self.known:
                self.known.add((kind, stream))
                self.with_db("track", lambda k=kind, st=stream, t=first: self.store.add_stream(k, st, t))
                log.info("now sweeping %s stream %s", kind, stream)

    # -- sweep path --

    def sweep_once(self) -> int:
        """Read history from each stream's checkpoint and store what is missing."""
        total = 0
        checkpoints = self.with_db("read checkpoints", self.store.checkpoints)
        self.known.update(checkpoints)
        end = self.clock() - self.s.settle
        for (kind_value, stream), through in sorted(checkpoints.items()):
            attr = next(a for a, v in KINDS.items() if v == kind_value)
            if attr not in self.s.kinds:
                continue
            start = through - self.s.overlap
            batch: list[Event] = []
            for event in getattr(self.client, attr).query_all(stream, start=start, end=end, page_size=1000):
                batch.append(event)
                if len(batch) >= self.s.batch_size:
                    total += self.with_db("sweep", lambda b=batch: self.store.write(b))
                    batch = []
                if self.stop.is_set():
                    return total  # checkpoint not moved: the next run repeats this window
            total += self.with_db("sweep", lambda b=batch, k=kind_value, st=stream:
                                  self.store.write(b, checkpoint=(k, st, end)))
        if total:
            log.info("sweep: stored %d events from history (published before start, or missed live)", total)
        self.stats["swept"] += total
        return total

    def sweeper(self) -> None:
        backoff = Backoff()
        while not self.stop.is_set():
            try:
                self.sweep_once()
                backoff.reset()
                wait = self.s.sweep_interval.total_seconds()
            except MeteorCloudError as err:
                wait = backoff.next()
                log.warning("sweep failed (%s), retrying in %.1fs", err, wait)
            except Exception as err:  # noqa: BLE001
                if self.stop.is_set():
                    return
                wait = backoff.next()
                log.warning("sweep failed (%s: %s), retrying in %.1fs", type(err).__name__, err, wait)
            self.stop.wait(wait)

    # -- lifecycle --

    def run(self) -> None:
        start = self.clock() - self.s.backfill
        # Concrete streams are swept from the start; first run backfills SINK_BACKFILL.
        for attr in self.s.kinds:
            for stream in self.s.streams:
                if not is_pattern(stream):
                    self.with_db("init", lambda k=KINDS[attr], st=stream: self.store.add_stream(k, st, start))

        subscriptions = []
        for attr in self.s.kinds:
            for stream in self.s.streams:
                subscriptions.append(getattr(self.client, attr).subscribe(stream, self.on_event))
                log.info("subscribed to %s %s", attr, stream)

        threads = [threading.Thread(target=self.writer, name="writer"),
                   threading.Thread(target=self.sweeper, name="sweeper")]
        for t in threads:
            t.start()
        try:
            while not self.stop.wait(1):
                pass
        finally:
            log.info("shutting down: closing subscriptions, flushing %d queued events", self.live.qsize())
            for sub in subscriptions:
                try:
                    sub.close()
                except Exception:  # noqa: BLE001
                    pass
            for t in threads:
                t.join(timeout=60)
            self.store.close()
            log.info("stopped (live %d, swept %d, dropped %d)", self.stats["live"], self.stats["swept"],
                     self.dropped)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too chatty
    settings = Settings.from_env()
    store = open_store(settings.database_url)
    with MeteorClient.from_env() as client:
        me = client.whoami()
        log.info("connected as application %s (organization %s)", me.application, me.organization_id)
        sink = Sink(settings, client, store)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: sink.stop.set())
        sink.run()


if __name__ == "__main__":
    main()
