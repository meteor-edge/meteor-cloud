"""Reconcile desired WiFi settings from the control plane.

Failures never raise out of ``reconcile_network`` — the agent loop must keep
heartbeating. State is persisted under the agent config directory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from edge_agent.client import AgentApiError, EdgeClient

logger = logging.getLogger(__name__)


def _state_path(config_dir: Path) -> Path:
    return config_dir / "wifi-applied.json"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def reconcile_network(
    client: EdgeClient,
    device_token: str,
    config_dir: Path,
) -> dict[str, Any] | None:
    """Fetch desired WiFi and apply when revision changes. Return wifi_report or None."""
    try:
        desired = client.get_network(device_token=device_token)
    except AgentApiError as error:
        logger.warning("Could not fetch desired network (%s)", error.code)
        return None
    except Exception:
        logger.exception("Unexpected error fetching desired network")
        return None

    state_path = _state_path(config_dir)
    state = _load_state(state_path)

    if not desired.get("configured"):
        if state.get("cleared"):
            return None
        _save_state(state_path, {"cleared": True})
        return {
            "revision": int(state.get("revision") or 0),
            "applied_ssid": None,
            "sync_status": "cleared",
            "sync_error": None,
        }

    try:
        revision = int(desired["revision"])
        ssid = str(desired["ssid"])
        psk = str(desired["psk"])
        interface = str(desired.get("interface") or "wlan0")
        hidden = bool(desired.get("hidden") or False)
    except (KeyError, TypeError, ValueError):
        logger.warning("Invalid desired network payload")
        return None

    if state.get("revision") == revision and state.get("sync_status") == "in_sync":
        return {
            "revision": revision,
            "applied_ssid": state.get("applied_ssid"),
            "sync_status": "in_sync",
            "sync_error": None,
        }

    try:
        from runtime.wifi import apply_wifi

        apply_wifi(ssid=ssid, psk=psk, interface=interface, hidden=hidden)
    except Exception as exc:  # noqa: BLE001 — never crash the agent loop
        message = getattr(exc, "message", None) or str(exc)
        logger.warning("WiFi apply failed: %s", message)
        report = {
            "revision": revision,
            "applied_ssid": None,
            "sync_status": "failed",
            "sync_error": str(message)[:500],
        }
        _save_state(state_path, report)
        return report

    report = {
        "revision": revision,
        "applied_ssid": ssid,
        "sync_status": "in_sync",
        "sync_error": None,
        "cleared": False,
    }
    _save_state(state_path, report)
    return {
        "revision": revision,
        "applied_ssid": ssid,
        "sync_status": "in_sync",
        "sync_error": None,
    }
