"""Stage 7b: device logs (sources listed on demand -> log session -> helper over SSH/WireGuard)."""

from __future__ import annotations

import json
import shlex

from e2e.http import WebSocket, WebSocketClosed
from e2e.redact import redact
from e2e.suite.framework import check, expect, wait_for
from e2e.suite.s7_apps import CONTAINER

STAGE = "7b device logs"
# [m] keeps pgrep from matching the shell that runs it.
HELPER_RUNNING = "pgrep -f '[m]eteorcli-logs show journal'"


def _sources_path(ctx) -> str:
    return ctx.org_path(f"/devices/{ctx.data['devices'][0]['id']}/log-sources")


def _open(ctx, body: dict) -> tuple[dict, WebSocket]:
    info = ctx.data["devices"][0]
    session = ctx.admin().call("POST", ctx.org_path(f"/devices/{info['id']}/log-sessions"), json_body=body,
                               expect=(201,))
    redact.add(session["ticket"])
    base = ctx.api_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    ws = WebSocket(f"{base}{session['websocket_path']}", ca_file=ctx.cfg.ca_file)
    ws.send(json.dumps({"type": "auth", "ticket": session["ticket"]}))
    return session, ws


def _read_all(ws: WebSocket, timeout: float = 60) -> bytes:
    """Everything until the server closes the socket (a log without --follow ends by itself)."""
    seen = b""
    try:
        while b"[end of log]" not in seen:
            seen += ws.read_until(b"\n", timeout=timeout)
    except WebSocketClosed:
        pass
    return seen


def _journal_marker(ctx, label: str) -> str:
    marker = f"E2E-LOGS-{label}-{ctx.run_id}"
    ctx.sh(ctx.device(0), f"logger -t meteorcloud-e2e {shlex.quote(marker)}")
    return marker


@check("logs.sources_reported", STAGE, "The device lists its log sources, including the deployed container",
       needs=("app.container_running", "wg.control_plane_reaches_device"))
def sources_reported(ctx):
    def listed():
        body = ctx.admin().call("GET", _sources_path(ctx))
        names = {(s["kind"], s["name"]) for s in body["sources"]}
        return body if ("docker", CONTAINER) in names else None

    body = wait_for(listed, timeout=60, interval=10, what=f"log sources with docker:{CONTAINER}")
    expect(f"docker:{CONTAINER}" in body["deployed"], f"{CONTAINER} is not marked deployed: {body['deployed']}")
    kinds = sorted({s["kind"] for s in body["sources"]})
    expect("journal" in kinds and "systemd" in kinds, f"journal/systemd sources missing: {kinds}")
    return {"sources": len(body["sources"]), "kinds": kinds}


@check("logs.journal_search", STAGE, "A journal search over a log session finds a line written on the device",
       needs=("logs.sources_reported", "wg.control_plane_reaches_device"))
def journal_search(ctx):
    marker = _journal_marker(ctx, "search")
    session, ws = _open(ctx, {"kind": "journal", "lines": 20, "grep": marker, "follow": False, "since": "15m"})
    ctx.data["log_session"] = session
    try:
        text = _read_all(ws).decode(errors="replace")
    finally:
        ws.close()
    expect(marker in text, f"marker not in the log output: {text[-400:]!r}")
    expect("sudo:" not in text and "meteorcli-logs:" not in text, f"the helper refused: {text[-400:]!r}")
    return {"bytes": len(text)}


@check("logs.follow_streams", STAGE, "A followed log streams a line written after the session opened",
       needs=("logs.journal_search",))
def follow_streams(ctx):
    marker = f"E2E-LOGS-follow-{ctx.run_id}"
    _session, ws = _open(ctx, {"kind": "journal", "lines": 1, "grep": marker, "follow": True})
    try:
        # Give the helper a moment to start following before writing the line.
        wait_for(lambda: ctx.sh(ctx.device(0), HELPER_RUNNING, check=False).stdout,
                 timeout=30, interval=1, what="the log helper to start")
        ctx.sh(ctx.device(0), f"logger -t meteorcloud-e2e {shlex.quote(marker)}")
        ws.read_until(marker.encode(), timeout=30)
    finally:
        ws.close()
    stopped = wait_for(
        lambda: not ctx.sh(ctx.device(0), HELPER_RUNNING, check=False).stdout.strip(),
        timeout=30, interval=2, what="the followed log to stop after the session closed",
    )
    return {"streamed": True, "stopped": stopped}


@check("logs.container_lines", STAGE, "The deployed container's log opens without errors",
       needs=("logs.journal_search",))
def container_lines(ctx):
    _session, ws = _open(ctx, {"kind": "docker", "name": CONTAINER, "lines": 50, "follow": False})
    try:
        text = _read_all(ws).decode(errors="replace")
    finally:
        ws.close()
    expect("[end of log]" in text, f"the log did not finish cleanly: {text[-400:]!r}")
    expect("sudo:" not in text and "meteorcli-logs:" not in text, f"the helper refused: {text[-400:]!r}")
    return {"bytes": len(text)}


@check("logs.certificate_forced", STAGE, "A log session's SSH certificate forces the helper command",
       needs=("logs.journal_search", "shell.certificate_login"))
def certificate_forced(ctx):
    session_id = ctx.data["log_session"]["session_id"]
    needle = shlex.quote(f"ID logs:{session_id}:")
    line = ctx.sh(ctx.device(0), f"journalctl --no-pager -q --since '-30 min' | grep -F {needle} || true",
                  check=False).stdout.strip()
    expect(line and "CERT" in line, f"the device's sshd did not log a logs: certificate: {line[-300:]!r}")
    return {"sshd": line.splitlines()[-1][-240:]}


@check("logs.unlisted_source_refused", STAGE, "The device's helper refuses a source it does not list",
       needs=("logs.sources_reported",))
def unlisted(ctx):
    _session, ws = _open(ctx, {"kind": "docker", "name": f"not-there-{ctx.run_id}", "follow": False})
    try:
        text = _read_all(ws).decode(errors="replace")
    finally:
        ws.close()
    expect("source not found" in text, f"the helper did not refuse the source: {text[-400:]!r}")
    return {"refused": True}
