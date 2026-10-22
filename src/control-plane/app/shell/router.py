"""Browser SSH: session tickets and WebSocket PTY bridge."""

from __future__ import annotations

import asyncio
import logging
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
    try:
        row, host, port, username = service.consume_ticket(session_id=session_id, ticket=ticket)
        connection = service._require_provider().connect(host=host, port=port, username=username)
    except AppError as exc:
        await websocket.send_text(f"error: {exc.message}")
        await websocket.close(code=4400, reason=exc.code[:120])
        return
    except Exception:
        logger.exception("Failed to open device shell")
        await websocket.send_text("error: could not open a shell to this device")
        await websocket.close(code=1011)
        return

    idle = service.settings.shell_idle_timeout_seconds
    max_duration = service.settings.shell_max_duration_seconds
    last_activity = time.monotonic()
    stop = asyncio.Event()

    async def from_device() -> None:
        nonlocal last_activity
        while not stop.is_set():
            chunk = await asyncio.to_thread(connection.read, 4096)
            if chunk:
                last_activity = time.monotonic()
                await websocket.send_bytes(chunk)
            elif time.monotonic() - last_activity > idle:
                await websocket.send_text("\r\n[idle timeout]\r\n")
                stop.set()
                return
            else:
                await asyncio.sleep(0.05)

    async def from_browser() -> None:
        nonlocal last_activity
        while not stop.is_set():
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                stop.set()
                return
            data = message.get("bytes")
            if data is None and message.get("text") is not None:
                data = str(message["text"]).encode("utf-8")
            if data:
                last_activity = time.monotonic()
                await asyncio.to_thread(connection.write, data)

    try:
        await asyncio.wait_for(
            asyncio.gather(from_device(), from_browser()),
            timeout=float(max_duration),
        )
    except TimeoutError:
        await websocket.send_text("\r\n[session time limit reached]\r\n")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Shell session %s ended with an error", session_id)
    finally:
        stop.set()
        connection.close()
        service.close_session(session_id)
        try:
            await websocket.close()
        except Exception:
            pass
