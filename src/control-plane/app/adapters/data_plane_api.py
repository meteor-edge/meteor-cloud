"""HTTP client for the application data plane's internal API (contracts/data-plane-v1.md)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any

from app.core.config import Settings
from app.core.exceptions import AppError


class DataPlaneHttpApi:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.data_plane_api_url.rstrip("/")
        self._token = settings.data_plane_internal_token
        self._timeout = 10.0

    def create_command(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", "/internal/v1/commands", body)
        assert result is not None
        return result

    def get_command(self, *, organization_id: uuid.UUID, command_id: uuid.UUID) -> dict[str, Any] | None:
        return self._request(
            "GET", f"/internal/v1/organizations/{organization_id}/commands/{command_id}", None, missing_ok=True
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None, *, missing_ok: bool = False
    ) -> dict[str, Any] | None:
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", "X-Data-Plane-Internal-Token": self._token},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                pass
            status = exc.code if 400 <= exc.code < 500 else 502
            raise AppError(
                "data_plane_error", detail or f"The data plane returned HTTP {exc.code}.", status_code=status
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AppError("data_plane_unreachable", "The data plane is unreachable.", status_code=503) from exc
