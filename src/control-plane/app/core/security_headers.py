"""Security headers on every API response.

A pure ASGI middleware (not ``BaseHTTPMiddleware``) so streaming downloads, server-sent
events and WebSockets pass through untouched. The console's own headers (CSP and the
rest) are set by its nginx (``console/nginx.conf``).
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)
# JSON never needs to run scripts or load anything. The interactive API docs are HTML
# that loads Swagger UI / ReDoc from a CDN, so they keep the browser default.
API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = SECURITY_HEADERS
        if not str(scope.get("path", "")).startswith(DOCS_PATHS):
            extra = (*SECURITY_HEADERS, API_CSP)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in extra if name not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
