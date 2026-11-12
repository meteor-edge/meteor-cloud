"""The live connection: one WebSocket carrying subscriptions and commands.

It reconnects on its own and restores every subscription after a reconnect.
Events published while the connection was down are not replayed over it; they
are in history and can be read with ``query``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from . import exceptions as exc
from ._http import HTTP, USER_AGENT, ssl_context
from .config import Config

log = logging.getLogger("meteorcloud")

CLOSE_SLOW_CONSUMER = 4008
CLOSE_UNAUTHORIZED = 4401


def _subscribe_error(code: str, message: str) -> exc.MeteorCloudError:
    if code == "forbidden_stream":
        return exc.PermissionDeniedError(message, code=code)
    return exc.ValidationError(message, code=code)


class StreamSubscription:
    """Server-side subscription tracked by the connection."""

    def __init__(self, sub_id: str, frame: dict[str, Any], deliver: Callable[[dict[str, Any]], Awaitable[None]],
                 fail: Callable[[exc.MeteorCloudError], None]) -> None:
        self.id = sub_id
        self.frame = frame
        self.deliver = deliver
        self.fail = fail
        self.confirmed: asyncio.Future[None] | None = None
        self.waiting = False


class Stream:
    def __init__(self, config: Config, http: HTTP) -> None:
        self._config = config
        self._http = http
        self._subs: dict[str, StreamSubscription] = {}
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._connected = asyncio.Event()
        self.on_command: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.identity: dict[str, Any] | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._closed:
            raise exc.ClientClosedError("The client is closed")
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="meteorcloud-stream")

    async def wait_connected(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        self._closed = True
        task, self._task = self._task, None
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), 5)
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ---- subscriptions -------------------------------------------------------

    async def subscribe(self, sub: StreamSubscription, timeout: float) -> None:
        """Register a subscription and wait for the service to confirm it.

        A refused subscription raises. If MeteorCloud cannot be reached right now the
        subscription stays registered and becomes active once the connection is up.
        """
        self._subs[sub.id] = sub
        sub.confirmed = asyncio.get_running_loop().create_future()
        self.start()
        # Set before sending: a refusal can arrive while the send is still yielding.
        sub.waiting = True
        try:
            if self._ws is not None and self.connected:
                await self._send({"op": "subscribe", "id": sub.id, **sub.frame})
            await asyncio.wait_for(asyncio.shield(sub.confirmed), timeout)
        except asyncio.CancelledError:
            # The caller gave up: nobody will read this subscription.
            if self._subs.pop(sub.id, None) is not None and self._ws is not None and self.connected:
                await asyncio.shield(self._send({"op": "unsubscribe", "id": sub.id}))
            raise
        except asyncio.TimeoutError:
            log.warning("subscription not confirmed yet; it becomes active when MeteorCloud is reachable",
                        extra={"meteorcloud": {"stream": sub.frame.get("stream")}})
        except exc.MeteorCloudError:
            self._subs.pop(sub.id, None)
            raise
        finally:
            sub.waiting = False

    async def unsubscribe(self, sub_id: str) -> None:
        if self._subs.pop(sub_id, None) is not None and self._ws is not None and self.connected:
            await self._send({"op": "unsubscribe", "id": sub_id})

    async def _send(self, frame: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(frame))
        except ConnectionClosed:
            pass  # the run loop reconnects and subscribes again

    # ---- connection loop -----------------------------------------------------

    def _url(self) -> str:
        endpoint = self._config.endpoint
        scheme = "wss://" if endpoint.startswith("https://") else "ws://"
        return scheme + endpoint.split("://", 1)[1] + "/v1/stream"

    async def _run(self) -> None:
        attempt = 0
        while not self._closed:
            reason = "connection lost"
            try:
                kwargs: dict[str, Any] = {}
                if self._url().startswith("wss://"):
                    context = ssl_context(self._config)
                    if context is not None:
                        kwargs["ssl"] = context
                async with connect(
                    self._url(),
                    additional_headers={"Authorization": f"Bearer {self._http.token()}"},
                    user_agent_header=USER_AGENT,
                    open_timeout=self._config.timeout,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=4 << 20,
                    **kwargs,
                ) as ws:
                    self._ws = ws
                    async for raw in ws:
                        try:
                            frame = json.loads(raw)
                        except ValueError:
                            continue
                        if frame.get("type") == "ready":
                            attempt = 0
                            self.identity = frame.get("identity")
                            self._connected.set()
                            log.info("live connection ready")
                            for sub in list(self._subs.values()):
                                await self._send({"op": "subscribe", "id": sub.id, **sub.frame})
                        else:
                            await self._dispatch(frame)
                    code = ws.close_code
                    if code == CLOSE_UNAUTHORIZED:
                        self._http.refresh_token()
                        reason = "credential revoked or expired"
                    elif code == CLOSE_SLOW_CONSUMER:
                        reason = "this application reads events too slowly; some live events were skipped"
            except asyncio.CancelledError:
                raise
            except InvalidStatus as err:
                status = err.response.status_code
                reason = f"refused with HTTP {status}"
                if status == 401:
                    self._http.refresh_token()
                    reason = "token refused"
            except ConnectionClosed as err:
                if err.rcvd is not None and err.rcvd.code == CLOSE_UNAUTHORIZED:
                    self._http.refresh_token()
                    reason = "credential revoked or expired"
            except exc.ConfigurationError as err:
                reason = err.message
            except (OSError, asyncio.TimeoutError) as err:
                reason = f"cannot reach MeteorCloud ({type(err).__name__})"
            except Exception as err:  # noqa: BLE001 - keep the connection loop alive
                reason = f"unexpected error ({type(err).__name__})"
                log.exception("live connection failed")
            finally:
                self._ws = None
                self._connected.clear()
            if self._closed:
                return
            delay = random.uniform(0.5, 1.0) * min(self._config.reconnect_max_delay, 0.5 * (2 ** min(attempt, 16)))
            attempt += 1
            log.warning("live connection down, reconnecting",
                        extra={"meteorcloud": {"reason": reason, "retry_in_seconds": round(delay, 2)}})
            await asyncio.sleep(delay)

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "event":
            sub = self._subs.get(frame.get("subscription", ""))
            if sub is not None:
                await sub.deliver(frame["event"])
        elif kind == "command":
            if self.on_command is not None:
                await self.on_command(frame["command"])
        elif kind == "subscribed":
            sub = self._subs.get(frame.get("id", ""))
            if sub is not None and sub.confirmed is not None and not sub.confirmed.done():
                sub.confirmed.set_result(None)
        elif kind == "error":
            error = _subscribe_error(str(frame.get("code", "")), str(frame.get("message", "")))
            sub = self._subs.get(frame.get("id", "")) if frame.get("id") else None
            if sub is None:
                log.error("live connection error", extra={"meteorcloud": {"code": error.code}})
                return
            if sub.waiting and sub.confirmed is not None and not sub.confirmed.done():
                sub.confirmed.set_exception(error)
            else:
                # Refused after a reconnect, e.g. the permission was removed meanwhile.
                self._subs.pop(sub.id, None)
                sub.fail(error)
