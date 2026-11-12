"""Asyncio client. :class:`meteorcloud.MeteorClient` is the same API without ``await``."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import signal
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import datetime
from typing import Any

import httpx

from . import exceptions as exc
from ._http import HTTP
from ._stream import Stream, StreamSubscription
from .config import Config, TokenSource
from .models import Bucket, Command, Event, Identity, Page, PublishResult, format_time, parse_time

log = logging.getLogger("meteorcloud")

EventCallback = Callable[[Event], Any]
CommandHandler = Callable[[Command], Any]

_MAX_BATCH = 500
_CLOSED = object()


def _new_id() -> str:
    return uuid.uuid4().hex


def _check_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise exc.ValidationError("data must be a dict (it is sent as a JSON object)", code="invalid_payload")
    try:
        json.dumps(data)
    except (TypeError, ValueError) as err:
        raise exc.ValidationError(f"data is not JSON serialisable: {err}", code="invalid_payload") from err
    return data


async def _call(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a user callback: coroutine functions are awaited, plain functions run in a thread."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    return await asyncio.to_thread(fn, *args)


class AsyncSubscription:
    """A live subscription. Iterate it, or give ``subscribe`` a callback.

    .. code-block:: python

        async with await client.telemetry.subscribe("energy.*") as sub:
            async for event in sub:
                ...
    """

    def __init__(self, client: AsyncMeteorClient, stream: str, callback: EventCallback | None) -> None:
        self.id = "s-" + _new_id()[:12]
        self.stream = stream
        self._client = client
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=client.config.subscription_buffer)
        self._callback = callback
        self._consumer: asyncio.Task[None] | None = None
        self._error: exc.MeteorCloudError | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def _deliver(self, raw: dict[str, Any]) -> None:
        if not self._closed:
            # A full buffer makes the connection wait, so events are never dropped
            # here. A reader that stays too slow is disconnected by the service and
            # the SDK reconnects.
            await self._queue.put(Event.from_dict(raw))

    def _fail(self, error: exc.MeteorCloudError) -> None:
        self._error = error
        self._finish()

    def _finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client._subscriptions.discard(self)
        try:
            self._queue.put_nowait(_CLOSED)
        except asyncio.QueueFull:
            # Make room for the end marker; the subscription is ending anyway.
            self._queue.get_nowait()
            self._queue.put_nowait(_CLOSED)

    def _start_consumer(self) -> None:
        self._consumer = asyncio.get_running_loop().create_task(self._consume(), name=f"meteorcloud-sub-{self.stream}")

    async def _consume(self) -> None:
        assert self._callback is not None
        async for event in self:
            try:
                await _call(self._callback, event)
            except Exception:  # noqa: BLE001 - one bad event must not end the subscription
                log.exception("subscription callback failed", extra={"meteorcloud": {"stream": event.stream}})

    async def get(self, timeout: float | None = None) -> Event:
        """Next event. Raises ``TimeoutError`` after ``timeout`` seconds without one."""
        if self._closed and self._queue.empty():
            raise self._error or exc.ClientClosedError("The subscription is closed")
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No event on {self.stream!r} within {timeout} seconds") from None
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)
            raise self._error or exc.ClientClosedError("The subscription is closed")
        return item

    def __aiter__(self) -> AsyncIterator[Event]:
        return self

    async def __anext__(self) -> Event:
        try:
            return await self.get()
        except exc.ClientClosedError:
            raise StopAsyncIteration from None

    async def close(self) -> None:
        if not self._closed:
            self._finish()
            await self._client._stream.unsubscribe(self.id)
        consumer, self._consumer = self._consumer, None
        if consumer is not None and consumer is not asyncio.current_task():
            await asyncio.wait({consumer}, timeout=self._client.config.timeout)

    async def __aenter__(self) -> AsyncSubscription:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


class AsyncStreams:
    """Publish, subscribe and query for one kind of data.

    ``client.telemetry``, ``client.events`` and ``client.inference`` are three of
    these. They behave the same and keep the three kinds of data apart.
    """

    def __init__(self, client: AsyncMeteorClient, kind: str) -> None:
        self._client = client
        self._kind = kind

    # ---- publish -------------------------------------------------------------

    def _message(self, stream: str, data: Any, time: datetime | str | None, id: str | None, seq: int | None,
                 type: str | None) -> dict[str, Any]:
        message: dict[str, Any] = {"id": id or _new_id(), "kind": self._kind, "stream": stream,
                                   "data": _check_data(data)}
        if time is not None:
            message["time"] = format_time(time)
        if seq is not None:
            message["seq"] = seq
        if type is not None:
            message["type"] = type
        return message

    async def publish(
        self,
        stream: str,
        data: dict[str, Any],
        *,
        time: datetime | str | None = None,
        id: str | None = None,
        seq: int | None = None,
        type: str | None = None,
    ) -> PublishResult:
        """Publish one message and return once it is durably stored.

        Raises :class:`PublishError` if the service rejects it and
        :class:`UnavailableError` if it could not be stored in time. Give your own
        ``id`` to make a publish safe to repeat after a crash: a message with an id
        that is already stored is not stored again.
        """
        results = await self._send([self._message(stream, data, time, id, seq, type)])
        result = results[0]
        if not result.ok:
            raise exc.PublishError(result.error_message or "The message was rejected", code=result.error_code or "")
        return result

    async def publish_many(self, stream: str, items: Iterable[dict[str, Any]]) -> list[PublishResult]:
        """Publish many messages to one stream.

        Each item is either the data dict itself or ``{"data": {...}, "time": ...,
        "id": ..., "seq": ..., "type": ...}``. Returns one result per item, in order.
        Rejected items do not raise: check ``result.ok``.
        """
        messages = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("data"), dict) and set(item) <= {
                "data", "time", "id", "seq", "type"
            }:
                messages.append(self._message(stream, item["data"], item.get("time"), item.get("id"),
                                              item.get("seq"), item.get("type")))
            else:
                messages.append(self._message(stream, item, None, None, None, None))
        results: list[PublishResult] = []
        for start in range(0, len(messages), _MAX_BATCH):
            results.extend(await self._send(messages[start:start + _MAX_BATCH]))
        return results

    async def _send(self, messages: list[dict[str, Any]]) -> list[PublishResult]:
        body = await self._client._http.request("POST", "/v1/messages", json={"messages": messages})
        results = []
        for sent, item in zip(messages, body["results"], strict=True):
            error = item.get("error") or {}
            results.append(PublishResult(id=item.get("id") or sent["id"], status=item["status"],
                                         error_code=error.get("code"), error_message=error.get("message")))
        return results

    # ---- subscribe -----------------------------------------------------------

    async def subscribe(
        self,
        stream: str,
        callback: EventCallback | None = None,
        *,
        device_id: str | None = None,
        application: str | None = None,
    ) -> AsyncSubscription:
        """Receive messages as they are stored.

        ``stream`` may be a pattern: ``*`` matches one name segment and a trailing
        ``**`` matches one or more (``energy.*``, ``alerts.**``). ``device_id`` and
        ``application`` narrow it to one publisher. With a ``callback`` the SDK calls
        it for each event; without one, iterate the returned subscription.
        """
        client = self._client
        client._check_open()
        sub = AsyncSubscription(client, stream, callback)
        frame = {"stream": stream, "kind": self._kind, "device_id": device_id or "", "application": application or ""}
        await client._stream.subscribe(
            StreamSubscription(sub.id, frame, sub._deliver, sub._fail), client.config.timeout)
        client._subscriptions.add(sub)
        if callback is not None:
            sub._start_consumer()
        return sub

    def on(self, stream: str, *, device_id: str | None = None, application: str | None = None):
        """Decorator form of :meth:`subscribe`. The subscription starts with ``client.run()``."""

        def register(fn: EventCallback) -> EventCallback:
            self._client._deferred.append(
                lambda: self.subscribe(stream, fn, device_id=device_id, application=application)
            )
            return fn

        return register

    # ---- history -------------------------------------------------------------

    def _params(self, start: Any, end: Any, device_id: str | None, application: str | None,
                where: dict[str, Any] | None) -> dict[str, Any]:
        params: dict[str, Any] = {"kind": self._kind}
        for key, value in (("start", format_time(start)), ("end", format_time(end)), ("device_id", device_id),
                           ("application", application)):
            if value:
                params[key] = value
        if where:
            params["filter"] = json.dumps(where)
        return params

    async def query(
        self,
        stream: str,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        limit: int = 100,
        order: str = "asc",
        device_id: str | None = None,
        application: str | None = None,
        where: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> Page:
        """One page of stored messages, oldest first (``order="desc"`` for newest first).

        ``where`` keeps only messages whose data contains these values, for example
        ``{"severity": "warning"}``. Pass ``page.next_cursor`` as ``cursor`` for the
        next page, or use :meth:`query_all`.
        """
        params = self._params(start, end, device_id, application, where)
        params.update({"limit": limit, "order": order})
        if cursor:
            params["cursor"] = cursor
        body = await self._client._http.request("GET", f"/v1/streams/{stream}/data", params=params)
        return Page([Event.from_dict(e) for e in body["events"]], body.get("next_cursor"))

    async def query_all(self, stream: str, *, page_size: int = 500, max_events: int | None = None,
                        **filters: Any) -> AsyncIterator[Event]:
        """Iterate every stored message that matches, fetching page after page."""
        cursor, seen = None, 0
        while True:
            page = await self.query(stream, limit=page_size, cursor=cursor, **filters)
            for event in page.events:
                yield event
                seen += 1
                if max_events is not None and seen >= max_events:
                    return
            cursor = page.next_cursor
            if not cursor:
                return

    async def aggregate(
        self,
        stream: str,
        field: str | None = None,
        *,
        function: str = "avg",
        bucket: str = "1m",
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        device_id: str | None = None,
        application: str | None = None,
        where: dict[str, Any] | None = None,
    ) -> list[Bucket]:
        """Aggregate one numeric field over time buckets.

        ``function`` is ``avg``, ``min``, ``max``, ``sum`` or ``count``. ``bucket`` is
        a width such as ``30s``, ``5m``, ``1h`` or ``1d``. Without ``start`` the
        window is the last 24 hours. ``field`` may be nested: ``phases.l1``.
        """
        params = self._params(start, end, device_id, application, where)
        params.update({"aggregate": function, "bucket": bucket})
        if field:
            params["field"] = field
        body = await self._client._http.request("GET", f"/v1/streams/{stream}/data", params=params)
        return [Bucket(start=parse_time(b["start"]), value=b.get("value"), count=b.get("count", 0))  # type: ignore[arg-type]
                for b in body["buckets"]]


class AsyncCommands:
    """Send commands to applications and handle commands sent to this one."""

    def __init__(self, client: AsyncMeteorClient) -> None:
        self._client = client
        self._handlers: dict[str, CommandHandler] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._finished: OrderedDict[str, tuple[str, dict[str, Any] | None, str]] = OrderedDict()
        self._slots = asyncio.Semaphore(8)

    # ---- sending -------------------------------------------------------------

    async def send(
        self,
        application: str,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        device_id: str | None = None,
        timeout: float | None = None,
        expires_in: float | None = None,
        max_attempts: int | None = None,
        idempotency_key: str | None = None,
    ) -> Command:
        """Send a command to an application and return at once.

        ``device_id`` targets the copy of the application on one device. ``timeout``
        is how long the target may take once it has the command; ``expires_in`` is
        how long MeteorCloud keeps trying to deliver it. Follow it with
        :meth:`wait` or use :meth:`call`.
        """
        body: dict[str, Any] = {"application": application, "name": name, "payload": _check_data(payload or {}),
                                "idempotency_key": idempotency_key or _new_id()}
        if device_id:
            body["device_id"] = device_id
        if timeout is not None:
            body["timeout_seconds"] = max(1, int(timeout))
        if expires_in is not None:
            body["expires_in_seconds"] = max(1, int(expires_in))
        if max_attempts is not None:
            body["max_attempts"] = max_attempts
        return Command.from_dict(await self._client._http.request("POST", "/v1/commands", json=body))

    async def get(self, command_id: str) -> Command:
        return Command.from_dict(await self._client._http.request("GET", f"/v1/commands/{command_id}"))

    async def wait(self, command_id: str, timeout: float | None = None) -> Command:
        """Wait until the command ends in SUCCEEDED, FAILED or TIMEOUT and return it."""
        deadline = None if timeout is None else time.monotonic() + timeout
        pause = 0.2
        while True:
            command = await self.get(command_id)
            if command.done:
                return command
            if deadline is not None and time.monotonic() + pause > deadline:
                raise exc.CommandTimeoutError(
                    f"Command {command_id} is still {command.state} after {timeout} seconds",
                    command=command, code="wait_timeout")
            await asyncio.sleep(pause)
            pause = min(pause * 1.5, 2.0)

    async def call(self, application: str, name: str, payload: dict[str, Any] | None = None, *,
                   device_id: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
        """Send a command, wait for it and return its result.

        Raises :class:`CommandError` if it failed or timed out.
        """
        command = await self.send(application, name, payload, device_id=device_id, timeout=timeout,
                                  expires_in=timeout)
        command = await self.wait(command.id, timeout=timeout + 15)
        if not command.succeeded:
            raise exc.CommandError(command.error or f"Command ended as {command.state}", command=command,
                                   code=command.state.lower())
        return command.result or {}

    # ---- receiving -----------------------------------------------------------

    def handler(self, name: str = "*"):
        """Decorator that registers the function handling commands called ``name``.

        The function receives the :class:`Command` and may return a dict, which
        becomes the command result. Raising marks the command as failed. ``"*"``
        handles every command without a handler of its own. Handling starts with
        ``client.run()`` (or ``client.start()``).

        A command can be delivered more than once (after a reconnect, for example),
        so handlers should be safe to repeat.
        """

        def register(fn: CommandHandler) -> CommandHandler:
            self.register(name, fn)
            return fn

        return register

    def register(self, name: str, fn: CommandHandler) -> None:
        self._handlers[name] = fn
        self._client._wants_stream = True

    async def _ack(self, command_id: str, status: str, result: dict[str, Any] | None = None, error: str = "") -> None:
        body: dict[str, Any] = {"status": status}
        if result is not None:
            body["result"] = result
        if error:
            body["error"] = error[:2000]
        await self._client._http.request("POST", f"/v1/commands/{command_id}/ack", json=body)

    async def _on_command(self, raw: dict[str, Any]) -> None:
        command = Command.from_dict(raw)
        if command.id in self._running:
            return
        if command.id in self._finished:
            # Delivered again because the final acknowledgement was lost: repeat the
            # acknowledgement, do not run the handler twice.
            status, result, error = self._finished[command.id]
            task = asyncio.get_running_loop().create_task(self._safe_ack(command.id, status, result, error))
        else:
            task = asyncio.get_running_loop().create_task(self._execute(command))
        self._running[command.id] = task
        task.add_done_callback(lambda _t, cid=command.id: self._running.pop(cid, None))

    async def _safe_ack(self, command_id: str, status: str, result: dict[str, Any] | None = None,
                        error: str = "") -> bool:
        try:
            await self._ack(command_id, status, result, error)
            return True
        except exc.MeteorCloudError as err:
            log.warning("command acknowledgement failed",
                        extra={"meteorcloud": {"command_id": command_id, "status": status, "error": err.code}})
            return False

    async def _execute(self, command: Command) -> None:
        handler = self._handlers.get(command.name) or self._handlers.get("*")
        if not await self._safe_ack(command.id, "received"):
            return  # it is delivered again later
        if handler is None:
            outcome: tuple[str, dict[str, Any] | None, str] = (
                "failed", None, f"This application has no handler for command {command.name!r}")
        else:
            async with self._slots:
                await self._safe_ack(command.id, "executing")
                try:
                    result = await _call(handler, command)
                    if result is not None and not isinstance(result, dict):
                        result = {"value": result}
                    json.dumps(result)
                    outcome = ("succeeded", result, "")
                except Exception as err:  # noqa: BLE001 - the failure is reported to the sender
                    log.exception("command handler failed",
                                  extra={"meteorcloud": {"command": command.name, "command_id": command.id}})
                    outcome = ("failed", None, f"{type(err).__name__}: {err}")
        self._finished[command.id] = outcome
        while len(self._finished) > 1000:
            self._finished.popitem(last=False)
        await self._safe_ack(command.id, *outcome)

    async def _drain(self, timeout: float) -> None:
        tasks = list(self._running.values())
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)


class AsyncMeteorClient:
    """MeteorCloud client for asyncio applications.

    .. code-block:: python

        async with AsyncMeteorClient.from_env() as client:
            await client.telemetry.publish("energy.metrics", {"power": 1200})
    """

    def __init__(self, config: Config | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
                 **settings: Any) -> None:
        if config is None:
            config = Config(**settings)
        elif settings:
            raise TypeError("Pass either a Config or keyword settings, not both")
        self.config = config
        self._http = HTTP(config, TokenSource(config), transport=transport)
        self._stream = Stream(config, self._http)
        self._subscriptions: set[AsyncSubscription] = set()
        self._deferred: list[Callable[[], Awaitable[Any]]] = []
        self._wants_stream = False
        self._closed = False
        self._stop: asyncio.Event | None = None
        self.telemetry = AsyncStreams(self, "telemetry")
        self.events = AsyncStreams(self, "event")
        self.inference = AsyncStreams(self, "inference")
        self.commands = AsyncCommands(self)
        self._stream.on_command = self.commands._on_command

    @classmethod
    def from_env(cls, **overrides: Any) -> AsyncMeteorClient:
        """Create a client from ``METEORCLOUD_*`` environment variables."""
        return cls(Config.from_env(**overrides))

    def _check_open(self) -> None:
        if self._closed:
            raise exc.ClientClosedError("The client is closed")

    @property
    def connected(self) -> bool:
        """True while the live connection (subscriptions and commands) is up."""
        return self._stream.connected

    async def whoami(self) -> Identity:
        """The identity and permissions MeteorCloud sees for this token."""
        return Identity.from_dict(await self._http.request("GET", "/v1/whoami"))

    async def start(self) -> None:
        """Start decorator subscriptions and command handling without blocking."""
        self._check_open()
        deferred, self._deferred = self._deferred, []
        for subscribe in deferred:
            await subscribe()
        if self._wants_stream:
            self._stream.start()

    async def run(self, *, handle_signals: bool = True) -> None:
        """Start, then block until :meth:`stop` is called or SIGINT/SIGTERM arrives. Closes the client."""
        self._stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        installed = []
        if handle_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self._stop.set)
                    installed.append(sig)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass  # not the main thread, or a platform without signal handlers
        try:
            await self.start()
            await self._stop.wait()
        finally:
            for sig in installed:
                loop.remove_signal_handler(sig)
            await self.close()

    def stop(self) -> None:
        """Make :meth:`run` return."""
        if self._stop is not None:
            self._stop.set()

    async def close(self, timeout: float = 10.0) -> None:
        """Shut down: let running command handlers finish, then close connections."""
        if self._closed:
            return
        self._closed = True
        await self.commands._drain(timeout)
        for sub in list(self._subscriptions):
            await sub.close()
        await self._stream.close()
        await self._http.close()

    async def __aenter__(self) -> AsyncMeteorClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
