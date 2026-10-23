"""Stage 6: browser SSH (shell ticket -> WebSocket -> SSH gateway over WireGuard)."""

from __future__ import annotations

import time

from e2e.http import WebSocket, WebSocketClosed
from e2e.redact import redact
from e2e.suite.framework import check, expect

STAGE = "6 browser ssh"


def _session(ctx) -> dict:
    info = ctx.data["devices"][0]
    session = ctx.admin().call("POST", ctx.org_path(f"/devices/{info['id']}/shell-sessions"), expect=(201,))
    redact.add(session["ticket"])
    return session


def _ws_url(ctx, session: dict, ticket: str) -> str:
    base = ctx.api_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return f"{base}{session['websocket_path']}?ticket={ticket}"


def _rejected(ctx, url: str) -> dict:
    ws = WebSocket(url, ca_file=ctx.cfg.ca_file)
    try:
        first = ws.recv()
        try:
            ws.recv()
        except WebSocketClosed as closed:
            return {"message": first.decode(errors="replace")[:200], "close_code": closed.code}
        return {"message": first.decode(errors="replace")[:200], "close_code": None}
    except WebSocketClosed as closed:
        return {"message": "", "close_code": closed.code}
    finally:
        ws.sock.close()


@check("shell.round_trip", STAGE, "A shell ticket opens a terminal on the device and runs a command",
       needs=("wg.control_plane_reaches_device",))
def round_trip(ctx):
    session = _session(ctx)
    ctx.data["shell_session"] = session
    ws = WebSocket(_ws_url(ctx, session, session["ticket"]), ca_file=ctx.cfg.ca_file)
    started = time.monotonic()
    try:
        # $((6*7)) proves a shell evaluated the command rather than echoing input back.
        ws.send(f"echo E2E-$((6*7))-{ctx.run_id}-$(cat /etc/hostname)-END\n")
        seen = ws.read_until(f"E2E-42-{ctx.run_id}-".encode(), timeout=45)
        seen += ws.read_until(b"-END", timeout=10)
        ws.send("exit\n")
    finally:
        ws.close()
    text = seen.decode(errors="replace")
    remote_host = text.rsplit(f"E2E-42-{ctx.run_id}-", 1)[1].split("-END", 1)[0].strip()
    expected = ctx.sh(ctx.device(0), "cat /etc/hostname", sudo=False).stdout.strip()
    expect(remote_host == expected, f"terminal ran on {remote_host!r}, expected device {expected!r}")
    return {"device_hostname": remote_host, "seconds": round(time.monotonic() - started, 1)}


@check("shell.ticket_reuse_rejected", STAGE, "A used shell ticket is rejected", needs=("shell.round_trip",))
def ticket_reuse(ctx):
    session = ctx.data["shell_session"]
    outcome = _rejected(ctx, _ws_url(ctx, session, session["ticket"]))
    expect(outcome["message"].startswith("error") or outcome["close_code"] in (4400, 4401),
           f"reused ticket was not rejected: {outcome}")
    return outcome


@check("shell.bad_ticket_rejected", STAGE, "A wrong shell ticket is rejected", needs=("shell.round_trip",))
def bad_ticket(ctx):
    session = _session(ctx)
    outcome = _rejected(ctx, _ws_url(ctx, session, "sh_e2e_not_a_real_ticket_000000"))
    expect(outcome["message"].startswith("error") or outcome["close_code"] in (4400, 4401),
           f"wrong ticket was not rejected: {outcome}")
    return outcome
