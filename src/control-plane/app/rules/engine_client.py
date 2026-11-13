"""HTTP client for the rule processor's internal API (rule tests, evaluation status)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.core.config import Settings
from app.core.exceptions import AppError, ValidationAppError


class RulesEngineClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.rules_engine_url.rstrip("/")
        self._token = settings.data_plane_internal_token
        self._timeout = 30.0

    def simulate(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/internal/rules/simulate", body)

    def status(self, rule_id: str, *, after: str = "", limit: int = 100) -> dict[str, Any]:
        query = urllib.parse.urlencode({"after": after, "limit": limit})
        return self._request("GET", f"/internal/rules/status/{rule_id}?{query}", None)

    def engine(self) -> dict[str, Any]:
        return self._request("GET", "/internal/rules/engine", None)

    def _request(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        if not self._base:
            raise AppError("rules_engine_not_configured", "The rule engine is not configured.", status_code=503)
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=json.dumps(body, default=str).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", "X-Data-Plane-Internal-Token": self._token},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode())
            except Exception:
                detail = {}
            if exc.code == 422:
                problems = "; ".join(
                    f"{p.get('path')}: {p.get('message')}" if p.get("path") else str(p.get("message"))
                    for p in detail.get("problems") or []
                )
                raise ValidationAppError(
                    "invalid_rule_test", problems or detail.get("error") or "Invalid test."
                ) from exc
            raise AppError("rules_engine_error", f"The rule engine returned HTTP {exc.code}.", status_code=502) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AppError("rules_engine_unreachable", "The rule engine is unreachable.", status_code=503) from exc
