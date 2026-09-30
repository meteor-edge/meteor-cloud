"""Best-effort collection of device inventory.

Every value is optional. Any file or system call that is unavailable is skipped
so the agent runs on minimal or unusual systems without failing.
"""

from __future__ import annotations

import os
import platform
import socket
from pathlib import Path
from typing import Any


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def read_os_release() -> tuple[str | None, str | None]:
    """Return (os_name, os_version) parsed from /etc/os-release."""
    content = _read_text("/etc/os-release")
    if not content:
        return None, None
    values: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip('"')
    name = values.get("NAME") or values.get("PRETTY_NAME") or values.get("ID")
    version = values.get("VERSION_ID") or values.get("VERSION")
    return name, version


def read_serial_number() -> str | None:
    for path in (
        "/sys/class/dmi/id/product_serial",
        "/sys/class/dmi/id/board_serial",
    ):
        value = _read_text(path)
        if value and value.lower() not in {"none", "to be filled by o.e.m."}:
            return value
    return None


def read_mac_addresses() -> list[str]:
    macs: list[str] = []
    net_dir = Path("/sys/class/net")
    try:
        interfaces = sorted(p.name for p in net_dir.iterdir())
    except OSError:
        return macs
    for iface in interfaces:
        if iface == "lo":
            continue
        address = _read_text(str(net_dir / iface / "address"))
        if address and address != "00:00:00:00:00:00":
            macs.append(address)
    return macs


def read_cpu() -> tuple[str | None, int | None]:
    """Return (cpu_model, cpu_cores)."""
    content = _read_text("/proc/cpuinfo")
    model: str | None = None
    cores = 0
    if content:
        for line in content.splitlines():
            if line.startswith("model name") and model is None:
                model = line.split(":", 1)[1].strip()
            if line.startswith("processor"):
                cores += 1
    if cores == 0:
        cores = os.cpu_count() or 0
    return model, (cores or None)


def read_memory_mb() -> int | None:
    content = _read_text("/proc/meminfo")
    if not content:
        return None
    for line in content.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) // 1024  # kB -> MB
    return None


def collect_inventory() -> dict[str, Any]:
    """Return a dictionary of collected inventory, omitting nothing structural."""
    os_name, os_version = read_os_release()
    cpu_model, cpu_cores = read_cpu()

    try:
        uname = os.uname()
        kernel_version = uname.release
        architecture = uname.machine
    except AttributeError:  # pragma: no cover - non-POSIX fallback
        kernel_version = platform.release() or None
        architecture = platform.machine() or None

    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = None

    return {
        "serial_number": read_serial_number(),
        "mac_addresses": read_mac_addresses(),
        "hostname": hostname,
        "os_name": os_name,
        "os_version": os_version,
        "kernel_version": kernel_version,
        "architecture": architecture,
        "cpu_model": cpu_model,
        "cpu_cores": cpu_cores,
        "memory_mb": read_memory_mb(),
    }
