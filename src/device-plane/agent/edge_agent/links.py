"""Best-effort host link snapshot for the console (ethernet / wifi only)."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

NMCLI = "/usr/bin/nmcli"
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_TYPE_MAP = {
    "ethernet": "ethernet",
    "802-3-ethernet": "ethernet",
    "wifi": "wifi",
    "802-11-wireless": "wifi",
}


def collect_network_links() -> list[dict[str, Any]]:
    """Return [{name, type, state, connection}, …]. Never raises."""
    try:
        return _from_nmcli() or _from_sysfs()
    except Exception:
        logger.debug("network link snapshot failed", exc_info=True)
        return []


def _from_nmcli() -> list[dict[str, Any]]:
    if not os.path.isfile(NMCLI) or not os.access(NMCLI, os.X_OK):
        return []
    proc = subprocess.run(
        [NMCLI, "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": SAFE_PATH, "LANG": "C", "LC_ALL": "C"},
    )
    if proc.returncode != 0:
        return []
    links: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        # nmcli -t escapes ':' as '\:'
        parts: list[str] = []
        buf = ""
        escaped = False
        for ch in line:
            if escaped:
                buf += ch
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == ":":
                parts.append(buf)
                buf = ""
            else:
                buf += ch
        parts.append(buf)
        if len(parts) < 3:
            continue
        name, raw_type, state = parts[0], parts[1], parts[2]
        connection = parts[3] if len(parts) > 3 else ""
        mapped = _TYPE_MAP.get(raw_type)
        if mapped is None or not name or name == "lo":
            continue
        if connection in {"", "--"}:
            connection = ""
        links.append(
            {
                "name": name,
                "type": mapped,
                "state": state,
                "connection": connection or None,
            }
        )
    return links


def _from_sysfs() -> list[dict[str, Any]]:
    net = os.path.join("/sys", "class", "net")
    if not os.path.isdir(net):
        return []
    links: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(net))
    except OSError:
        return []
    for name in names:
        if name == "lo" or name.startswith(("veth", "docker", "br-", "wg")):
            continue
        base = os.path.join(net, name)
        wireless = os.path.isdir(os.path.join(base, "wireless")) or os.path.islink(
            os.path.join(base, "phy80211")
        )
        link_type = "wifi" if wireless else "ethernet"
        # Skip obvious non-physical without wireless and without device symlink? keep simple.
        oper = "unknown"
        try:
            with open(os.path.join(base, "operstate"), encoding="utf-8") as handle:
                oper = handle.read().strip() or "unknown"
        except OSError:
            pass
        state = "connected" if oper == "up" else ("disconnected" if oper == "down" else oper)
        links.append({"name": name, "type": link_type, "state": state, "connection": None})
    return links
