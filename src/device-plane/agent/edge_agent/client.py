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
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Sent on artifact downloads: this agent can follow a redirect to object storage.
REDIRECT_HEADER = "X-Meteorcli-Redirect"
# Sent on every API call (contracts/device-agent-v1.md): the contract version this agent speaks.
PROTOCOL_HEADER = "X-Meteor-Agent-Protocol"
PROTOCOL_VERSION = "1"


def user_agent() -> str:
    """``meteorcli/<version>``: lets the server tell agent versions apart in logs and metrics."""
    try:
        from meteorcli import __version__
    except ImportError:
        return "meteorcli/0.0.0"
    return f"meteorcli/{__version__}"


def _agent_headers() -> dict[str, str]:
    return {"User-Agent": user_agent(), PROTOCOL_HEADER: PROTOCOL_VERSION}
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand redirects back to the caller, so the device token never follows one."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_DOWNLOAD_OPENER = urllib.request.build_opener(_NoRedirect)


class AgentApiError(Exception):
    """Raised when the server returns a non-success response."""

    def __init__(self, status: int, code: str, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        # Seconds from the server's Retry-After header (429/503), when it sent one.
        self.retry_after = retry_after


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
        headers = {"Accept": "application/json", **_agent_headers()}
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
        except ValueError as exc:
            # A captive portal or proxy answered instead of MeteorCloud; retry like a network failure.
            raise AgentApiError(0, "bad_response", f"{url} did not answer with JSON") from exc
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
        retry_after = None
        raw_retry = (exc.headers.get("Retry-After") if exc.headers is not None else None) or ""
        if raw_retry.strip().isdigit():
            retry_after = int(raw_retry.strip())
        return AgentApiError(exc.code, code, message, retry_after=retry_after)

    def register(
        self,
        *,
        token: str,
        inventory: dict[str, Any],
        name: str | None,
        device_token: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"token": token, **inventory}
        if name:
            body["name"] = name
        if device_token:
            # Proves this is the device already registered for this hardware.
            body["device_token"] = device_token
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

    def get_mqtt_status(self, *, device_token: str) -> dict[str, Any]:
        return self._get("/api/v1/agent/mqtt", bearer=device_token)

    def fetch_mqtt_credentials(self, *, device_token: str) -> dict[str, Any]:
        return self._post("/api/v1/agent/mqtt/credentials", {}, bearer=device_token)

    def mint_app_credential(self, *, device_token: str, workload_key: str) -> dict[str, Any]:
        """Device-scoped application token for a workload bound to a MeteorCloud application."""
        return self._post("/api/v1/agent/applications/credentials", {"workload_key": workload_key}, bearer=device_token)

    def get_wifi_changes(self, *, device_token: str) -> list[dict[str, Any]]:
        """Wi-Fi changes waiting for this device. The answer can hold a Wi-Fi password: do not log it."""
        result = self._get("/api/v1/agent/network/wifi", bearer=device_token)
        changes = result.get("changes") if isinstance(result, dict) else None
        return changes if isinstance(changes, list) else []

    def report_wifi(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/agent/network/wifi/report", payload, bearer=device_token)

    def get_actions(self, *, device_token: str) -> list[dict[str, Any]]:
        """Actions waiting for this device. The answer can hold a password: do not log it."""
        result = self._get("/api/v1/agent/actions", bearer=device_token)
        actions = result.get("actions") if isinstance(result, dict) else None
        return actions if isinstance(actions, list) else []

    def report_actions(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/agent/actions/report", payload, bearer=device_token)

    def get_software(self, *, device_token: str) -> list[dict[str, Any]]:
        """Fetch desired software for this device."""
        result = self._get("/api/v1/agent/software", bearer=device_token)
        if isinstance(result, list):
            return result
        return []

    def get_signing_keys(self, *, device_token: str) -> list[dict[str, Any]] | None:
        """Organization signing keys, or None when the control plane does not sign (404)."""
        try:
            result = self._get("/api/v1/agent/signing-keys", bearer=device_token)
        except AgentApiError as error:
            if error.status == 404:
                return None
            raise
        return result if isinstance(result, list) else []

    def download_artifact(
        self,
        *,
        device_token: str,
        artifact_id: str,
        destination: Path,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> str:
        """Download an artifact, verify ``X-Checksum-SHA256``, and return the hex digest.

        The server may answer with a redirect to a short-lived object-storage URL
        (``X-Meteorcli-Redirect: 1`` says this agent can follow one). The checksum then
        comes from the redirect, and the storage URL is fetched without the device token.

        Raises ``AgentApiError`` with code ``checksum_mismatch`` when the digest
        does not match. Partial files are removed on failure.
        """
        url = f"{self.server_url}/api/v1/agent/artifacts/{artifact_id}/download"
        headers = {
            "Authorization": f"Bearer {device_token}",
            "Accept": "*/*",
            REDIRECT_HEADER: "1",
            **_agent_headers(),
        }
        request = urllib.request.Request(url, headers=headers, method="GET")
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = destination.with_suffix(destination.suffix + ".partial")
        try:
            redirect_checksum = ""
            try:
                response = _DOWNLOAD_OPENER.open(request, timeout=self.download_timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in _REDIRECT_CODES:
                    raise
                response, redirect_checksum = self._follow_storage_redirect(url, exc)
            with response:
                expected = (redirect_checksum or response.headers.get("X-Checksum-SHA256") or "").strip().lower()
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
            tmp_path.unlink(missing_ok=True)
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

    def _follow_storage_redirect(self, api_url: str, redirect: urllib.error.HTTPError) -> tuple[Any, str]:
        """Open the object-storage URL a download redirected to, without the device token.

        Returns the open response and the checksum the server sent with the redirect.
        """
        headers = redirect.headers
        location = (headers.get("Location") if headers is not None else None) or ""
        checksum = ((headers.get("X-Checksum-SHA256") if headers is not None else None) or "").strip().lower()
        redirect.close()
        if not location:
            raise AgentApiError(redirect.code, "download_redirect_invalid", "The download redirect has no location.")
        if len(checksum) != 64:
            # Without the server's checksum the storage copy cannot be checked: refuse it.
            raise AgentApiError(0, "checksum_missing", "The download redirect did not include a SHA-256 checksum.")
        target = urllib.parse.urljoin(api_url, location)
        scheme = urllib.parse.urlsplit(target).scheme
        if scheme not in ("https", "http") or (scheme == "http" and api_url.startswith("https://")):
            raise AgentApiError(0, "download_redirect_insecure", "Refusing to download over an insecure redirect.")
        storage_request = urllib.request.Request(target, headers={"Accept": "*/*"}, method="GET")
        return _DOWNLOAD_OPENER.open(storage_request, timeout=self.download_timeout), checksum
