"""WiFi apply via the meteorcli-wifi root helper (NetworkManager)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HELPER_NAMES = ("meteorcli-wifi",)
HELPER_DIRS = (
    Path("/usr/local/libexec"),
    Path("/usr/libexec"),
)
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TIMEOUT = 60


class WifiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _helper_path() -> Path | None:
    for directory in HELPER_DIRS:
        for name in HELPER_NAMES:
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    which = shutil.which("meteorcli-wifi")
    return Path(which) if which else None


def wifi_available() -> bool:
    """True when NetworkManager wifi is present (helper inspect or nmcli device)."""
    helper = _helper_path()
    if helper is None:
        return False
    try:
        result = _run_helper({"op": "inspect"})
    except WifiError:
        return False
    return bool(result.get("ok") and result.get("nmcli") and result.get("wifi"))


def _run_helper(payload: dict[str, Any]) -> dict[str, Any]:
    helper = _helper_path()
    if helper is None:
        raise WifiError("meteorcli-wifi helper is not installed")
    argv = [str(helper)]
    if os.geteuid() != 0:
        argv = ["sudo", "-n", str(helper)]
    try:
        proc = subprocess.run(
            argv,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
            env={"PATH": SAFE_PATH, "LANG": "C", "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise WifiError("wifi helper timed out") from exc
    except OSError as exc:
        raise WifiError(f"wifi helper failed to start: {exc}") from exc

    raw = (proc.stdout or "").strip().splitlines()
    last = raw[-1] if raw else ""
    try:
        data = json.loads(last) if last else {}
    except json.JSONDecodeError:
        data = {}
    if proc.returncode != 0 or not data.get("ok"):
        message = data.get("error") or (proc.stderr or proc.stdout or "wifi helper failed")
        raise WifiError(str(message).strip()[:500])
    return data


def apply_wifi(
    *,
    ssid: str,
    psk: str,
    interface: str = "wlan0",
    hidden: bool = False,
) -> dict[str, Any]:
    return _run_helper(
        {
            "op": "apply",
            "ssid": ssid,
            "psk": psk,
            "interface": interface,
            "hidden": hidden,
        }
    )
