"""Stdlib HTTP, TLS and WebSocket clients. TLS is always verified (system CAs plus E2E_CA_FILE)."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any


def tls_context(ca_file: str = "") -> ssl.SSLContext:
    context = ssl.create_default_context()
    if ca_file:
        context.load_verify_locations(ca_file)
    return context


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str = ""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def request(method: str, url: str, *, body: bytes | None = None, headers: dict[str, str] | None = None,
            timeout: float = 30, ca_file: str = "") -> Response:
    """One request, no redirect following."""
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname, port, timeout=timeout, context=tls_context(ca_file))
    else:
        conn = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    try:
        conn.request(method, path, body=body, headers=headers or {})
        raw = conn.getresponse()
        return Response(raw.status, {k.lower(): v for k, v in raw.getheaders()}, raw.read(), url)
    finally:
        conn.close()


def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "e2e" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, data, content_type) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class ApiError(Exception):
    def __init__(self, response: Response) -> None:
        super().__init__(f"HTTP {response.status} {response.url}: {response.text[:300]}")
        self.response = response


@dataclass
class Api:
    """MeteorCloud REST client (the same public API the console uses)."""

    base_url: str
    ca_file: str = ""
    token: str = ""
    timeout: float = 60
    last: Response | None = field(default=None, repr=False)

    def call(self, method: str, path: str, *, json_body: Any = None, form: bytes | None = None,
             content_type: str = "", expect: tuple[int, ...] = (200, 201, 204)) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            body = form
            headers["Content-Type"] = content_type
        response = request(method, self.base_url + path, body=body, headers=headers, timeout=self.timeout,
                           ca_file=self.ca_file)
        self.last = response
        if expect and response.status not in expect:
            raise ApiError(response)
        try:
            return response.json()
        except ValueError:
            return response.body

    def status(self, method: str, path: str, **kwargs: Any) -> int:
        """HTTP status only (for negative checks)."""
        self.call(method, path, expect=(), **kwargs)
        assert self.last is not None
        return self.last.status

    def login(self, email: str, password: str) -> str:
        data = self.call("POST", "/api/v1/auth/login", json_body={"email": email, "password": password})
        self.token = data["access_token"]
        return self.token

    def with_token(self, token: str) -> Api:
        return Api(self.base_url, self.ca_file, token, self.timeout)


# -- probes -------------------------------------------------------------------------

def resolve_ipv4(name: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)}
    except socket.gaierror:
        return set()


def tcp_open(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def peer_certificate(host: str, port: int = 443, *, ca_file: str = "", timeout: float = 10) -> dict[str, Any]:
    """Verified handshake (hostname + chain); raises ssl.SSLError otherwise."""
    context = tls_context(ca_file)
    with socket.create_connection((host, port), timeout=timeout) as raw, \
            context.wrap_socket(raw, server_hostname=host) as tls:
        cert = tls.getpeercert() or {}
    issuer = dict(item[0] for item in cert.get("issuer", ()))
    subject = dict(item[0] for item in cert.get("subject", ()))
    return {
        "subject": subject.get("commonName", ""),
        "issuer": f"{issuer.get('organizationName', '')} {issuer.get('commonName', '')}".strip(),
        "not_after": cert.get("notAfter", ""),
        "san": [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"],
    }


# -- WebSocket (RFC 6455 client, enough for the browser terminal) -----------------------

class WebSocketClosed(Exception):
    def __init__(self, code: int | None, reason: str) -> None:
        super().__init__(f"closed {code} {reason}")
        self.code = code
        self.reason = reason


class WebSocket:
    def __init__(self, url: str, *, ca_file: str = "", timeout: float = 20) -> None:
        parsed = urllib.parse.urlsplit(url)
        secure = parsed.scheme == "wss"
        port = parsed.port or (443 if secure else 80)
        raw = socket.create_connection((parsed.hostname, port), timeout=timeout)
        self.sock = tls_context(ca_file).wrap_socket(raw, server_hostname=parsed.hostname) if secure else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        host = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Origin: {'https' if secure else 'http'}://{host}\r\n\r\n".encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake: connection closed")
            head += chunk
        header, self._buffer = head.split(b"\r\n\r\n", 1)
        status_line = header.split(b"\r\n", 1)[0].decode()
        if " 101 " not in status_line + " ":
            raise ConnectionError(f"WebSocket handshake failed: {status_line}")
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if expected not in header:
            raise ConnectionError("WebSocket handshake: bad Sec-WebSocket-Accept")

    def _read(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketClosed(None, "connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def send(self, payload: bytes | str, opcode: int | None = None) -> None:
        data = payload.encode() if isinstance(payload, str) else payload
        if opcode is None:
            opcode = 0x1 if isinstance(payload, str) else 0x2
        header = bytes([0x80 | opcode])
        length = len(data)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> bytes:
        """Next data frame; raises WebSocketClosed on a close frame."""
        while True:
            first, second = self._read(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if second & 0x80 else b""
            data = self._read(length)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None
                raise WebSocketClosed(code, data[2:].decode("utf-8", "replace"))
            if opcode == 0x9:
                self.send(data, opcode=0xA)
                continue
            if opcode in (0x0, 0x1, 0x2):
                return data

    def read_until(self, marker: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        seen = b""
        while marker not in seen:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"marker not seen in {timeout:.0f}s")
            self.sock.settimeout(remaining)
            try:
                seen += self.recv()
            except TimeoutError:
                raise TimeoutError(f"marker not seen in {timeout:.0f}s") from None
        return seen

    def close(self) -> None:
        try:
            self.send(struct.pack("!H", 1000), opcode=0x8)
        except OSError:
            pass
        self.sock.close()
