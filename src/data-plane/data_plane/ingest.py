"""Forward inbound MQTT payloads to the control-plane ingest API."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from data_plane.config import Settings

logger = logging.getLogger(__name__)


def ingest_message(settings: Settings, *, topic: str, payload: str) -> None:
    data = json.dumps({"topic": topic, "payload": payload}).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.control_plane_url.rstrip('/')}/internal/mqtt/ingest",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-MQTT-Internal-Token": settings.mqtt_internal_token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"control-plane ingest HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        logger.warning("control-plane ingest HTTP %s for %s", exc.code, topic)
    except urllib.error.URLError:
        logger.warning("control-plane ingest unreachable for %s", topic)
