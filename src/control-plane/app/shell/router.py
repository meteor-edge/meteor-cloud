"""Browser SSH and device logs: session tickets and the WebSocket bridge to ssh."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.exceptions import AppError
from app.identity.dependencies import CurrentUser
from app.shell.dependencies import ShellSvc
from app.shell.logs import LogSessionCreateRequest, LogSourcesResponse
from app.shell.models import SessionKind
from app.shell.schemas import ShellSessionCreateResponse
from app.shell.service import ShellService

logger = logging.getLogger(__name__)

T = TypeVar("T")

router = APIRouter(tags=["shell"])

# How long a socket opened without a ticket may wait for its {"type": "auth"} frame.
AUTH_FRAME_TIMEOUT_SECONDS = 5.0


async def _ticket_from_first_frame(websocket: WebSocket) -> str | None:
    """Read ``{"type": "auth", "ticket": "..."}``, which keeps the ticket out of URLs and access logs."""
    try:
        message = await asyncio.wait_for(websocket.receive_text(), AUTH_FRAME_TIMEOUT_SECONDS)
        frame = json.loads(message)
    except (TimeoutError, ValueError, KeyError, WebSocketDisconnect):
        return None
    if not isinstance(frame, dict) or frame.get("type") != "auth":
        return None
    ticket = frame.get("ticket")
    return ticket if isinstance(ticket, str) and ticket else None


@router.post(
    "/api/v1/organizations/{organization_id}/devices/{device_id}/shell-sessions",
    response_model=ShellSessionCreateResponse,
    status_code=201,
)
def create_shell_session(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: ShellSvc,
) -> ShellSessionCreateResponse:
    return service.create_session(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
    )


@router.get(
    "/api/v1/organizations/{organization_id}/devices/{device_id}/log-sources",
    response_model=LogSourcesResponse,
)
def list_log_sources(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: ShellSvc,
) -> LogSourcesResponse:
    return service.log_sources(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post(
    "/api/v1/organizations/{organization_id}/devices/{device_id}/log-sessions",
    response_model=ShellSessionCreateResponse,
    status_code=201,
)
def create_log_session(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: LogSessionCreateRequest,
    current_user: CurrentUser,
    service: ShellSvc,
) -> ShellSessionCreateResponse:
    return service.create_log_session(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
        request=payload,
    )


@router.websocket("/api/v1/shell-sessions/{session_id}/ws")
async def shell_websocket(
    websocket: WebSocket,
    session_id: uuid.UUID,
    service: ShellSvc,
) -> None:
    await _bridge(websocket, session_id, service, kind=SessionKind.SHELL.value)


@router.websocket("/api/v1/log-sessions/{session_id}/ws")
async def log_websocket(
    websocket: WebSocket,
    session_id: uuid.UUID,
    service: ShellSvc,
) -> None:
    """Device log output as binary frames; text frames are status lines. Browser input is ignored."""
    await _bridge(websocket, session_id, service, kind=SessionKind.LOGS.value)


async def _bridge(websocket: WebSocket, session_id: uuid.UUID, service: ShellService, *, kind: str) -> None:
    logs = kind == SessionKind.LOGS.value
    # ?ticket= is still accepted for older clients, but it ends up in access logs.
    ticket = websocket.query_params.get("ticket") or websocket.headers.get("x-shell-ticket")
    await websocket.accept()
    if not ticket:
        ticket = await _ticket_from_first_frame(websocket)
    if not ticket:
        await websocket.close(code=4401, reason="Missing shell ticket")
        return
    # Redeeming the ticket, starting ssh (and signing its certificate) and the periodic access
    # check run in threads so they never stall the event loop. The request Session is not
    # thread-safe and a cancelled to_thread call can still be running, so every use holds this lock.
    db_lock = threading.Lock()

    def locked(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        with db_lock:
            return fn(*args, **kwargs)

    consumed = False
    connection = None
    device_id = None
    try:
        target = await asyncio.to_thread(
            locked, service.consume_ticket, session_id=session_id, ticket=ticket, kind=kind
        )
        consumed = True
        device_id = target.session.device_id
        connection = await asyncio.to_thread(
            service._require_provider().connect,
            host=target.host,
            port=target.port,
            username=target.username,
            host_key=target.host_key,
            certificate=target.certificate,
            command=target.command,
        )
    except AppError as exc:
        if consumed:
            locked(service.close_session, session_id)
        await websocket.send_text(f"error: {exc.message}")
        await websocket.close(code=4400, reason=exc.code[:120])
        return
    except Exception:
        if consumed:
            locked(service.close_session, session_id)
        logger.exception("Failed to open device shell")
        await websocket.send_text(f"error: could not open {'logs' if logs else 'a shell'} on this device")
        await websocket.close(code=1011)
        return
    if connection is None:
        await websocket.close(code=1011)
        return

    settings = service.settings
    idle = settings.shell_idle_timeout_seconds
    started = time.monotonic()
    last_activity = started

    async def from_device() -> str | None:
        nonlocal last_activity
        while True:
            chunk = await asyncio.to_thread(connection.read, 4096)
            if chunk:
                last_activity = time.monotonic()
                await websocket.send_bytes(chunk)
            elif connection.closed:
                # The shell exited (e.g. `exit`) or the log command finished: end the session now.
                return "\r\n[end of log]\r\n" if logs and not connection.failure else None
            else:
                await asyncio.sleep(0.05)

    async def from_browser() -> str | None:
        nonlocal last_activity
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return None
            data = message.get("bytes")
            if data is None and message.get("text") is not None:
                data = str(message["text"]).encode("utf-8")
            if data and not logs:
                last_activity = time.monotonic()
                await asyncio.to_thread(connection.write, data)

    async def watchdog() -> str:
        """Idle and duration limits, and access removed while the shell is open."""
        next_check = started + settings.shell_recheck_seconds
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            # A followed log can stay quiet for a long time; only the overall limit applies.
            if not logs and now - last_activity > idle:
                return "\r\n[idle timeout]\r\n"
            if now - started > settings.shell_max_duration_seconds:
                return "\r\n[session time limit reached]\r\n"
            if now >= next_check:
                next_check = now + settings.shell_recheck_seconds
                if not await asyncio.to_thread(locked, service.session_still_allowed, session_id):
                    return "\r\n[session ended: access to this device was removed]\r\n"

    tasks = [
        asyncio.create_task(from_device()),
        asyncio.create_task(from_browser()),
        asyncio.create_task(watchdog()),
    ]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                raise exc
            reason = task.result()
            if reason:
                await websocket.send_text(reason)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("%s session %s ended with an error", kind, session_id)
    finally:
        for task in tasks:
            task.cancel()
        # Cleanup stays synchronous so it still runs to the end if this coroutine is cancelled
        # (it is quick: ssh exits on SIGTERM, then two small writes). The lock waits for a
        # recheck that may still be running in a worker thread.
        connection.close()
        with db_lock:
            # Diagnostics only: ssh's own error line, or that the device answered.
            failure = connection.failure
            if device_id is not None and (failure or connection.received_output):
                service.connectivity.record_ssh_result(device_id, error=failure)
            service.close_session(session_id)
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close()
        except Exception:
            pass
