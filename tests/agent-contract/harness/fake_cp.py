"""A fake control plane: scripted answers on /api/v1/agent/*, every exchange recorded and checked.

Each request and answer is checked against contracts/schemas/agent while the scenario
runs. Problems are collected in ``violations`` (the test fails on any), so a wrong
field name shows up with the exact message that broke the contract.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from harness import schemas
from harness.transcript import binary_body

AGENT_PREFIX = "/api/v1/agent/"
USER_AGENT_RE = re.compile(r"^meteorcli/\d+\.\d+\.\d+")


@dataclass
class Request:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: Any  # parsed JSON, or None

    @property
    def bearer(self) -> str | None:
        value = self.headers.get("authorization", "")
        return value[7:] if value.startswith("Bearer ") else None


@dataclass
class Reply:
    """One scripted answer. ``off_contract`` names why a reply deliberately breaks the schema."""

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    off_contract: str | None = None


Handler = Callable[[Request], Reply]


@dataclass
class Exchange:
    request: Request
    reply: Reply
    at: float = field(default_factory=time.monotonic)  # arrival, for timing checks (not recorded)


class FakeControlPlane:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], deque[Reply | Handler]] = {}
        self._lock = threading.Lock()
        self.exchanges: list[Exchange] = []
        self.violations: list[str] = []
        # Answers every request when set (replay mode), instead of the scripted routes.
        self.responder: Handler | None = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, name="fake-cp", daemon=True)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    # ------------------------------------------------------------- scripting
    def on(self, method: str, path: str, *replies: Reply | Handler) -> None:
        """Answer ``method path`` with ``replies`` in order; the last one repeats."""
        self._routes[(method, path)] = deque(replies)

    def reset_route(self, method: str, path: str) -> None:
        self._routes.pop((method, path), None)

    def calls(self, method: str, path: str) -> list[Exchange]:
        with self._lock:
            return [e for e in self.exchanges if e.request.method == method and e.request.path == path]

    # ------------------------------------------------------------- lifecycle
    def start(self) -> FakeControlPlane:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # --------------------------------------------------------------- serving
    def _answer(self, request: Request) -> Reply:
        if self.responder is not None:
            return self.responder(request)
        queue = self._routes.get((request.method, request.path))
        if not queue:
            return Reply(404, {"error": {"code": "not_found", "message": f"{request.method} {request.path} is not scripted"}})
        step = queue.popleft() if len(queue) > 1 else queue[0]
        return step(request) if callable(step) else step

    def _check(self, request: Request, reply: Reply) -> None:
        problems: list[str] = []
        where = f"{request.method} {request.path}"
        if request.path.startswith(AGENT_PREFIX):
            op = schemas.http_operation(request.method, request.path)
            if op is None:
                problems.append(f"{where}: not a route in contracts/schemas/agent/index.json")
            else:
                if op["request"] and request.method != "GET":
                    problems += schemas.problems(op["request"], request.body)
                if reply.off_contract is None:
                    if reply.status >= 400:
                        problems += schemas.problems(schemas.index()["error"], reply.body)
                    elif str(reply.status) not in op["responses"]:
                        problems.append(f"{where}: status {reply.status} not in the contract")
                    elif op["responses"][str(reply.status)]:
                        problems += schemas.problems(op["responses"][str(reply.status)], reply.body)
            if not USER_AGENT_RE.match(request.headers.get("user-agent", "")):
                problems.append(f"{where}: User-Agent must be meteorcli/<version>, got {request.headers.get('user-agent')!r}")
            if request.headers.get("x-meteor-agent-protocol") != "1":
                problems.append(f"{where}: X-Meteor-Agent-Protocol must be 1")
            if "token" in request.query or "key" in request.query:
                problems.append(f"{where}: credentials must travel in headers, not the query string")
        if request.path.startswith("/storage/") and "authorization" in request.headers:
            problems.append(f"{where}: the device token must not follow a redirect to object storage")
        with self._lock:
            self.violations += [f"{where}: {p}" if not p.startswith(where) else p for p in problems]

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        plane = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # quiet
                return

            def _serve(self) -> None:
                path, _, query = self.path.partition("?")
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = {"<not json>": raw.decode("utf-8", "replace")}
                request = Request(self.command, path, query, {k.lower(): v for k, v in self.headers.items()}, body)
                reply = plane._answer(request)
                plane._check(request, reply)
                with plane._lock:
                    plane.exchanges.append(Exchange(request, reply))
                if isinstance(reply.body, (bytes, bytearray)):
                    payload, content_type = bytes(reply.body), "application/octet-stream"
                elif reply.body is None:
                    payload, content_type = b"", "application/json"
                else:
                    payload, content_type = json.dumps(reply.body).encode(), "application/json"
                self.send_response(reply.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

        return _Handler


def recorded_http(exchanges: list[Exchange], normalizer: Any) -> list[dict[str, Any]]:
    """The exchanges in order, normalized for a recording."""
    out = []
    for exchange in exchanges:
        req, reply = exchange.request, exchange.reply
        inventory = req.path in {
            "/api/v1/agent/register", "/api/v1/agent/enroll/request", "/api/v1/agent/heartbeat",
        }
        if isinstance(reply.body, (bytes, bytearray)):
            reply_body: Any = binary_body(bytes(reply.body))
        else:
            reply_body = normalizer.value(reply.body)
        entry: dict[str, Any] = {
            "request": {
                "method": req.method,
                "path": normalizer.text(req.path),
                # Off the agent API (health, object storage) only the token's presence is contract;
                # the HTTP client's own User-Agent (Python-urllib/3.x, Go-http-client) is not.
                "headers": normalizer.headers(req.headers) if req.path.startswith(AGENT_PREFIX)
                else {k: v for k, v in normalizer.headers(req.headers).items() if k == "authorization"},
                "body": normalizer.value(req.body, inventory=inventory),
            },
            "response": {
                "status": reply.status,
                "headers": {k: normalizer.text(v) for k, v in reply.headers.items()},
                "body": reply_body,
            },
        }
        if reply.off_contract:
            entry["response"]["off_contract"] = reply.off_contract
        out.append(entry)
    return out
