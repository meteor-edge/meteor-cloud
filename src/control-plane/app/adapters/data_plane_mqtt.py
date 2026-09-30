"""HTTP client for the data-plane MQTT gateway."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.core.config import Settings


class DataPlaneMQTTGateway:
    """Control-plane adapter: publish/watch via the data-plane HTTP API."""

    def __init__(self, settings: Settings) -> None:
        self._base = settings.data_plane_url.rstrip("/")
        self._token = settings.mqtt_internal_token
        self._timeout = 20.0

    def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        self._request(
            "POST",
            "/v1/publish",
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain},
        )

    def watch_topic(self, topic: str) -> None:
        self._request("POST", "/v1/subscriptions", {"topic": topic})

    def unwatch_topic(self, topic: str) -> None:
        self._request("POST", "/v1/subscriptions/unwatch", {"topic": topic})

    def _request(self, method: str, path: str, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-MQTT-Internal-Token": self._token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                if response.status >= 400:
                    raise RuntimeError(f"data-plane returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"data-plane returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("data-plane is unreachable") from exc
