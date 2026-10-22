"""Browser SSH: session tickets and WebSocket PTY bridge."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.exceptions import AppError
from app.identity.dependencies import CurrentUser
from app.shell.dependencies import ShellSvc
from app.shell.schemas import ShellSessionCreateResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shell"])


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


@router.websocket("/api/v1/shell-sessions/{session_id}/ws")
async def shell_websocket(
    websocket: WebSocket,
    session_id: uuid.UUID,
    service: ShellSvc,
) -> None:
    ticket = websocket.query_params.get("ticket") or websocket.headers.get("x-shell-ticket")
    await websocket.accept()
    if not ticket:
        await websocket.close(code=4401, reason="Missing shell ticket")
        return
    # The request Session is not thread-safe; a cancelled to_thread call can still be
    # running when the next one starts, so serialize every DB call on it.
    db_lock = threading.Lock()

    def locked(fn, /, *args, **kwargs):
        with db_lock:
            return fn(*args, **kwargs)

    provider = None
    try:
        # DB and subprocess work stays off the event loop.
        row, host, port, username = await asyncio.to_thread(
            locked, service.consume_ticket, session_id=session_id, ticket=ticket
        )
        provider = service._require_provider()
        connection = await asyncio.to_thread(
            provider.connect, host=host, port=port, username=username, device_id=row.device_id
        )
    except AppError as exc:
        await websocket.send_text(f"error: {exc.message}")
        await websocket.close(code=4400, reason=exc.code[:120])
        return
    except Exception:
        logger.exception("Failed to open device shell")
        if provider is not None:
            await asyncio.to_thread(locked, service.close_session, session_id)
        await websocket.send_text("error: could not open a shell to this device")
        await websocket.close(code=1011)
        return

    settings = service.settings
    idle = settings.shell_idle_timeout_seconds
    recheck = max(1, settings.shell_recheck_seconds)
    started = time.monotonic()
    last_activity = started
    reason: str | None = None

    async def from_device() -> str:
        nonlocal last_activity
        while True:
            chunk = await asyncio.to_thread(connection.read, 4096)
            if chunk:
                last_activity = time.monotonic()
                await websocket.send_bytes(chunk)
                continue
            if connection.is_closed():
                return "\r\n[connection closed]\r\n"
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
            if data:
                last_activity = time.monotonic()
                await asyncio.to_thread(connection.write, data)

    async def watchdog() -> str:
        # Idle and max-duration limits, plus revoke/disable while the shell is open.
        next_recheck = time.monotonic() + recheck
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            if now - last_activity > idle:
                return "\r\n[idle timeout]\r\n"
            if now - started > settings.shell_max_duration_seconds:
                return "\r\n[session time limit reached]\r\n"
            if now >= next_recheck:
                next_recheck = now + recheck
                if not await asyncio.to_thread(locked, service.session_still_allowed, session_id):
                    return "\r\n[session ended: device access was revoked]\r\n"

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
            if exc is None:
                reason = task.result()
            elif not isinstance(exc, WebSocketDisconnect):
                logger.error("Shell session %s ended with an error", session_id, exc_info=exc)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(connection.close)
        await asyncio.to_thread(locked, service.close_session, session_id)
        try:
            if reason:
                await websocket.send_text(reason)
            await websocket.close()
        except Exception:
            pass
