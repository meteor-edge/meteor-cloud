"""Minimal JSON HTTP client for the Edge Platform agent API.

Uses only the standard library. The client is language-neutral: it sends and
receives plain JSON and never assumes anything about the server implementation.
Credentials passed here are never logged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class AgentApiError(Exception):
    """Raised when the server returns a non-success response."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class EdgeClient:
    def __init__(self, server_url: str, *, timeout: float = 15.0) -> None:
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.server_url}{path}"
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise self._to_api_error(exc) from exc
        except urllib.error.URLError as exc:  # network-level failure
            raise AgentApiError(0, "network_error", str(exc.reason)) from exc

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, body, bearer=bearer)

    def _get(self, path: str, *, bearer: str | None = None) -> dict[str, Any]:
        return self._request("GET", path, bearer=bearer)

    def health(self) -> dict[str, Any]:
        return self._get("/api/v1/health")

    def check_api_key(self, *, api_key: str) -> dict[str, Any]:
        return self._post("/api/v1/agent/enroll/check", {}, bearer=api_key)

    def enroll_request(
        self,
        *,
        api_key: str,
        inventory: dict[str, Any],
        name: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {**inventory}
        if name:
            body["name"] = name
        return self._post("/api/v1/agent/enroll/request", body, bearer=api_key)

    def enroll_poll(self, *, request_id: str, claim_secret: str) -> dict[str, Any]:
        return self._post(
            "/api/v1/agent/enroll/poll",
            {"request_id": request_id, "claim_secret": claim_secret},
        )

    @staticmethod
    def _to_api_error(exc: urllib.error.HTTPError) -> AgentApiError:
        code = "request_failed"
        message = f"Request failed with status {exc.code}"
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            error = payload.get("error", {})
            code = error.get("code", code)
            message = error.get("message", message)
        except (ValueError, AttributeError, OSError):
            pass
        return AgentApiError(exc.code, code, message)

    def register(
        self,
        *,
        token: str,
        inventory: dict[str, Any],
        name: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"token": token, **inventory}
        if name:
            body["name"] = name
        return self._post("/api/v1/agent/register", body)

    def heartbeat(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/agent/heartbeat", payload, bearer=device_token)
