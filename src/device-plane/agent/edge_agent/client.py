"""Minimal JSON HTTP client for the Edge Platform agent API.

Uses only the standard library. The client is language-neutral: it sends and
receives plain JSON and never assumes anything about the server implementation.
Credentials passed here are never logged.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


class AgentApiError(Exception):
    """Raised when the server returns a non-success response."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class EdgeClient:
    def __init__(
        self,
        server_url: str,
        *,
        timeout: float = 15.0,
        download_timeout: float = 300.0,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self.download_timeout = download_timeout

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
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            # Read timeouts and dropped connections surface outside URLError; retry them the same way.
            raise AgentApiError(0, "network_error", str(exc) or type(exc).__name__) from exc

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

    def register_wireguard(
        self,
        *,
        device_token: str,
        public_key: str,
        ssh_user: str | None = None,
        ssh_host_key: str | None = None,
        ssh_ca_public_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"public_key": public_key}
        if ssh_user:
            body["ssh_user"] = ssh_user
        if ssh_host_key:
            body["ssh_host_key"] = ssh_host_key
        if ssh_ca_public_key:
            body["ssh_ca_public_key"] = ssh_ca_public_key
        return self._post(
            "/api/v1/agent/connectivity/wireguard",
            body,
            bearer=device_token,
        )

    def get_software(self, *, device_token: str) -> list[dict[str, Any]]:
        """Fetch desired software for this device."""
        result = self._get("/api/v1/agent/software", bearer=device_token)
        if isinstance(result, list):
            return result
        return []

    def download_artifact(
        self,
        *,
        device_token: str,
        artifact_id: str,
        destination: Path,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> str:
        """Download an artifact, verify ``X-Checksum-SHA256``, and return the hex digest.

        Raises ``AgentApiError`` with code ``checksum_mismatch`` when the digest
        does not match. Partial files are removed on failure.
        """
        url = f"{self.server_url}/api/v1/agent/artifacts/{artifact_id}/download"
        headers = {"Authorization": f"Bearer {device_token}", "Accept": "*/*"}
        request = urllib.request.Request(url, headers=headers, method="GET")
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination.with_suffix(destination.suffix + ".partial")
        try:
            with urllib.request.urlopen(request, timeout=self.download_timeout) as response:
                expected = (response.headers.get("X-Checksum-SHA256") or "").strip().lower()
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header and total_header.isdigit() else None
                digest = hashlib.sha256()
                received = 0
                with tmp_path.open("wb") as handle:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if on_progress is not None:
                            on_progress(received, total)
            actual = digest.hexdigest()
            if expected and actual != expected:
                tmp_path.unlink(missing_ok=True)
                raise AgentApiError(
                    0,
                    "checksum_mismatch",
                    "Downloaded artifact failed SHA-256 verification.",
                )
            tmp_path.replace(destination)
            return expected or actual
        except AgentApiError:
            raise
        except urllib.error.HTTPError as exc:
            tmp_path.unlink(missing_ok=True)
            raise self._to_api_error(exc) from exc
        except urllib.error.URLError as exc:
            tmp_path.unlink(missing_ok=True)
            raise AgentApiError(0, "network_error", str(exc.reason)) from exc
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
