"""HTTP transport: authentication, retries with backoff, error mapping."""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
import time
from typing import Any

import httpx

from . import exceptions as exc
from .config import Config, TokenSource

log = logging.getLogger("meteorcloud")

USER_AGENT = "meteorcloud-python/0.1.0"

# A token that was just issued or rotated may take a few seconds to reach the data
# plane, so a 401 shortly after loading a token is retried instead of failing.
_FRESH_TOKEN_WINDOW = 60.0
_FRESH_TOKEN_GRACE = 15.0

_RETRY_STATUS = {429, 502, 503, 504}


def ssl_context(config: Config) -> ssl.SSLContext | None:
    if not config.ca_file:
        return None
    return ssl.create_default_context(cafile=config.ca_file)


def backoff(attempt: int, cap: float = 5.0, base: float = 0.2) -> float:
    """Exponential backoff with full jitter."""
    return random.uniform(0, min(cap, base * (2**attempt)))


def error_from_response(status: int, body: Any) -> exc.MeteorCloudError:
    code, message = "", ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = str(body["error"].get("code", ""))
        message = str(body["error"].get("message", ""))
    message = message or f"MeteorCloud answered HTTP {status}"
    if status == 401:
        cls: type[exc.MeteorCloudError] = exc.AuthenticationError
    elif status == 403:
        cls = exc.PermissionDeniedError
    elif status == 404:
        cls = exc.NotFoundError
    elif status == 429:
        cls = exc.RateLimitedError
    elif status >= 500:
        cls = exc.UnavailableError
    else:
        cls = exc.ValidationError
    return cls(message, code=code, status=status)


class HTTP:
    def __init__(self, config: Config, tokens: TokenSource, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = config
        self._tokens = tokens
        self._token_seen: str | None = None
        self._token_loaded_at = 0.0
        verify: Any = ssl_context(config) or True
        self._client = httpx.AsyncClient(
            base_url=config.endpoint,
            timeout=httpx.Timeout(config.timeout),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            verify=verify,
            transport=transport,
        )

    def token(self) -> str:
        token = self._tokens.get()
        if token != self._token_seen:
            self._token_seen = token
            self._token_loaded_at = time.monotonic()
        return token

    def refresh_token(self) -> bool:
        changed = self._tokens.refresh()
        self.token()
        return changed

    def token_is_fresh(self) -> bool:
        return time.monotonic() - self._token_loaded_at < _FRESH_TOKEN_WINDOW

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        retry_time: float | None = None,
    ) -> Any:
        """Send one request, retrying what is safe to retry.

        Every call the SDK makes is idempotent (messages carry ids, commands carry an
        idempotency key), so network errors, overload and rate limits are retried
        until ``retry_time`` runs out.
        """
        budget = self._config.retry_time if retry_time is None else retry_time
        deadline = time.monotonic() + budget
        auth_deadline: float | None = None
        attempt = 0
        while True:
            last: exc.MeteorCloudError
            delay = backoff(attempt)
            try:
                response = await self._client.request(
                    method, path, json=json, params=params, headers={"Authorization": f"Bearer {self.token()}"}
                )
            except httpx.TransportError as err:
                last = exc.UnavailableError(f"Cannot reach MeteorCloud: {type(err).__name__}", code="unreachable")
                last.__cause__ = err
            else:
                if response.status_code < 300:
                    return response.json() if response.content else None
                try:
                    body = response.json()
                except ValueError:
                    body = None
                last = error_from_response(response.status_code, body)
                if response.status_code == 401:
                    changed = self.refresh_token()
                    now = time.monotonic()
                    if auth_deadline is None:
                        auth_deadline = now + _FRESH_TOKEN_GRACE
                    if not (changed or self.token_is_fresh()) or now >= auth_deadline:
                        raise last
                    delay = max(delay, 1.0)
                elif response.status_code not in _RETRY_STATUS:
                    raise last
                else:
                    retry_after = response.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        delay = max(delay, min(float(retry_after), 30.0))
            attempt += 1
            if time.monotonic() + delay >= deadline:
                raise last
            log.warning(
                "request failed, retrying",
                extra={"meteorcloud": {"path": path, "attempt": attempt, "error": last.code or type(last).__name__}},
            )
            await asyncio.sleep(delay)
