"""Blocking client: the API of :class:`meteorcloud.AsyncMeteorClient` without ``await``.

It runs the asyncio client on a background thread, so both clients share one
implementation and behave the same. It is safe to use from several threads.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import signal
import threading
from collections.abc import Callable, Coroutine, Iterable, Iterator
from datetime import datetime
from typing import Any, TypeVar

import httpx

from . import exceptions as exc
from .aio import (
    IN_HANDLER,
    AsyncCommands,
    AsyncMeteorClient,
    AsyncStreams,
    AsyncSubscription,
    CommandHandler,
    EventCallback,
)
from .config import Config
from .models import Bucket, Command, Event, Identity, Message, Page, PublishResult

T = TypeVar("T")


class Subscription:
    """A live subscription. Iterate it, or give ``subscribe`` a callback.

    .. code-block:: python

        with client.telemetry.subscribe("energy.*") as sub:
            for event in sub:
                ...
    """

    def __init__(self, client: MeteorClient, inner: AsyncSubscription) -> None:
        self._client = client
        self._inner = inner
        self.id = inner.id
        self.stream = inner.stream

    @property
    def closed(self) -> bool:
        return self._inner.closed

    def get(self, timeout: float | None = None) -> Event:
        """Next event. Raises ``TimeoutError`` after ``timeout`` seconds without one."""
        return self._client._do(self._inner.get(timeout))

    def __iter__(self) -> Iterator[Event]:
        return self

    def __next__(self) -> Event:
        try:
            return self.get()
        except exc.ClientClosedError:
            raise StopIteration from None

    def close(self) -> None:
        self._client._do(self._inner.close())

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class Streams:
    """Publish, subscribe and query for one kind of data (see :class:`AsyncStreams`)."""

    def __init__(self, client: MeteorClient, inner: AsyncStreams) -> None:
        self._client = client
        self._inner = inner

    def publish(self, stream: str, data: dict[str, Any], *, time: datetime | str | None = None,
                id: str | None = None, seq: int | None = None, type: str | None = None) -> PublishResult:
        return self._client._do(self._inner.publish(stream, data, time=time, id=id, seq=seq, type=type))

    def publish_many(self, stream: str, items: Iterable[dict[str, Any] | Message]) -> list[PublishResult]:
        return self._client._do(self._inner.publish_many(stream, list(items)))

    def subscribe(self, stream: str, callback: EventCallback | None = None, *, device_id: str | None = None,
                  application: str | None = None) -> Subscription:
        inner = self._client._do(
            self._inner.subscribe(stream, callback, device_id=device_id, application=application))
        return Subscription(self._client, inner)

    def on(self, stream: str, *, device_id: str | None = None,
           application: str | None = None) -> Callable[[EventCallback], EventCallback]:
        return self._inner.on(stream, device_id=device_id, application=application)

    def query(self, stream: str, *, start: datetime | str | None = None, end: datetime | str | None = None,
              limit: int = 100, order: str = "asc", device_id: str | None = None, application: str | None = None,
              where: dict[str, Any] | None = None, cursor: str | None = None) -> Page:
        return self._client._do(self._inner.query(
            stream, start=start, end=end, limit=limit, order=order, device_id=device_id, application=application,
            where=where, cursor=cursor))

    def query_all(self, stream: str, *, page_size: int = 500, max_events: int | None = None,
                  **filters: Any) -> Iterator[Event]:
        cursor, seen = None, 0
        while True:
            page = self.query(stream, limit=page_size, cursor=cursor, **filters)
            for event in page.events:
                yield event
                seen += 1
                if max_events is not None and seen >= max_events:
                    return
            cursor = page.next_cursor
            if not cursor:
                return

    def aggregate(self, stream: str, field: str | None = None, *, function: str = "avg", bucket: str = "1m",
                  start: datetime | str | None = None, end: datetime | str | None = None,
                  device_id: str | None = None, application: str | None = None,
                  where: dict[str, Any] | None = None) -> list[Bucket]:
        return self._client._do(self._inner.aggregate(
            stream, field, function=function, bucket=bucket, start=start, end=end, device_id=device_id,
            application=application, where=where))

    publish.__doc__ = AsyncStreams.publish.__doc__
    publish_many.__doc__ = AsyncStreams.publish_many.__doc__
    subscribe.__doc__ = AsyncStreams.subscribe.__doc__
    on.__doc__ = AsyncStreams.on.__doc__
    query.__doc__ = AsyncStreams.query.__doc__
    query_all.__doc__ = AsyncStreams.query_all.__doc__
    aggregate.__doc__ = AsyncStreams.aggregate.__doc__


class Commands:
    """Send commands to applications and handle commands sent to this one."""

    def __init__(self, client: MeteorClient, inner: AsyncCommands) -> None:
        self._client = client
        self._inner = inner

    def send(self, application: str, name: str, payload: dict[str, Any] | None = None, *,
             device_id: str | None = None, timeout: float | None = None, expires_in: float | None = None,
             max_attempts: int | None = None, idempotency_key: str | None = None) -> Command:
        return self._client._do(self._inner.send(
            application, name, payload, device_id=device_id, timeout=timeout, expires_in=expires_in,
            max_attempts=max_attempts, idempotency_key=idempotency_key))

    def get(self, command_id: str) -> Command:
        return self._client._do(self._inner.get(command_id))

    def wait(self, command_id: str, timeout: float | None = None) -> Command:
        return self._client._do(self._inner.wait(command_id, timeout))

    def call(self, application: str, name: str, payload: dict[str, Any] | None = None, *,
             device_id: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
        return self._client._do(self._inner.call(application, name, payload, device_id=device_id, timeout=timeout))

    def handler(self, name: str = "*") -> Callable[[CommandHandler], CommandHandler]:
        return self._inner.handler(name)

    def register(self, name: str, fn: CommandHandler) -> None:
        self._inner.register(name, fn)

    send.__doc__ = AsyncCommands.send.__doc__
    wait.__doc__ = AsyncCommands.wait.__doc__
    call.__doc__ = AsyncCommands.call.__doc__
    handler.__doc__ = AsyncCommands.handler.__doc__


class MeteorClient:
    """MeteorCloud client.

    .. code-block:: python

        from meteorcloud import MeteorClient

        client = MeteorClient.from_env()
        client.telemetry.publish("energy.metrics", {"power": 1200, "voltage": 230})
    """

    def __init__(self, config: Config | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
                 **settings: Any) -> None:
        if config is None:
            config = Config(**settings)
        elif settings:
            raise TypeError("Pass either a Config or keyword settings, not both")
        self.config = config
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="meteorcloud", daemon=True)
        self._thread.start()
        self._closed = False
        self._lock = threading.Lock()  # guards _closed against calls racing with close()
        self._stop = threading.Event()
        self._async: AsyncMeteorClient = self._do(self._create(config, transport))
        self.telemetry = Streams(self, self._async.telemetry)
        self.events = Streams(self, self._async.events)
        self.inference = Streams(self, self._async.inference)
        self.commands = Commands(self, self._async.commands)

    @staticmethod
    async def _create(config: Config, transport: httpx.AsyncBaseTransport | None) -> AsyncMeteorClient:
        return AsyncMeteorClient(config, transport=transport)

    @classmethod
    def from_env(cls, **overrides: Any) -> MeteorClient:
        """Create a client from ``METEORCLOUD_*`` environment variables."""
        return cls(Config.from_env(**overrides))

    def _do(self, coro: Coroutine[Any, Any, T]) -> T:
        if threading.current_thread() is self._thread:
            coro.close()
            raise RuntimeError(
                "The blocking client cannot be used from an async callback. Use a plain function as the "
                "callback, or use AsyncMeteorClient.")
        # Checked and submitted under one lock: nothing is queued on a loop that
        # close() is about to stop.
        with self._lock:
            if self._closed:
                coro.close()
                raise exc.ClientClosedError("The client is closed")
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            raise exc.ClientClosedError("The client was closed during the call") from None

    @property
    def connected(self) -> bool:
        """True while the live connection (subscriptions and commands) is up."""
        return self._async.connected

    def whoami(self) -> Identity:
        """The identity and permissions MeteorCloud sees for this token."""
        return self._do(self._async.whoami())

    def start(self) -> None:
        """Start decorator subscriptions and command handling without blocking."""
        self._do(self._async.start())

    def run(self, *, handle_signals: bool = True) -> None:
        """Start, then block until :meth:`stop` is called or SIGINT/SIGTERM arrives. Closes the client."""
        previous: dict[int, Any] = {}
        if handle_signals and threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.signal(sig, lambda *_: self._stop.set())
        try:
            self.start()
            while not self._stop.wait(0.5):
                pass
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            self.close()

    def stop(self) -> None:
        """Make :meth:`run` return."""
        self._stop.set()

    def close(self, timeout: float = 10.0) -> None:
        """Shut down: let running command handlers finish, then close connections."""
        if IN_HANDLER.get():
            # Called from a command handler: closing now would wait for this very
            # handler and then cut off its result. Close once the handler is done.
            self._stop.set()
            threading.Thread(target=self.close, args=(timeout,), name="meteorcloud-close", daemon=True).start()
            return
        with self._lock:
            if self._closed:
                return
            self._closed = True  # from here on no new call is accepted
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(timeout), self._loop).result()
        finally:
            self._stop.set()
            self._loop.call_soon_threadsafe(self._loop.stop)
            if threading.current_thread() is not self._thread:
                self._thread.join(timeout=5)
            if not self._loop.is_running():
                self._loop.close()

    async def _shutdown(self, timeout: float) -> None:
        try:
            await self._async.close(timeout)
        finally:
            # Calls that were still in flight end with ClientClosedError instead of
            # waiting on a loop that no longer runs.
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def __enter__(self) -> MeteorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
