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


# Firmware placeholders that identify nothing.
_PLACEHOLDER_VALUES = frozenset(
    {
        "default string",
        "none",
        "not applicable",
        "not specified",
        "system product name",
        "system serial number",
        "to be filled by o.e.m.",
        "unknown",
    }
)


def _first_real_value(paths: tuple[str, ...]) -> str | None:
    for path in paths:
        # Device-tree strings end with a NUL byte.
        value = (_read_text(path) or "").replace("\x00", "").strip()
        if value and value.lower() not in _PLACEHOLDER_VALUES and value.strip("0"):
            return value
    return None


def read_serial_number() -> str | None:
    """Board serial: DMI on PCs, the device tree on ARM boards such as the Raspberry Pi."""
    return _first_real_value(
        (
            "/sys/class/dmi/id/product_serial",
            "/sys/class/dmi/id/board_serial",
            "/sys/firmware/devicetree/base/serial-number",
        )
    )


def read_hardware_model() -> str | None:
    """Board model, e.g. ``Raspberry Pi 5 Model B Rev 1.0`` (device tree) or a DMI product name."""
    return _first_real_value(
        (
            "/sys/firmware/devicetree/base/model",
            "/sys/class/dmi/id/product_name",
            "/sys/class/dmi/id/board_name",
        )
    )


_VIRTUAL_IFACE_PREFIXES = (
    "lo",
    "docker",
    "veth",
    "br-",
    "cni",
    "flannel",
    "virbr",
    "wg",
    "tun",
    "tap",
)
_WIRED_IFACE_PREFIXES = ("eth", "en", "end")
_WIFI_IFACE_PREFIXES = ("wlan", "wl")


def _sys_class_net() -> Path:
    return Path("/sys/class/net")


def _is_virtual_iface(name: str) -> bool:
    if name == "lo":
        return True
    return name.startswith(_VIRTUAL_IFACE_PREFIXES)


def _iface_preference(name: str) -> int:
    if name.startswith(_WIRED_IFACE_PREFIXES):
        return 0
    if name.startswith(_WIFI_IFACE_PREFIXES):
        return 1
    return 2


def _iface_mac(net_dir: Path, iface: str) -> str | None:
    address = _read_text(str(net_dir / iface / "address"))
    if address and address != "00:00:00:00:00:00":
        return address
    return None


def read_mac_addresses() -> list[str]:
    """Return the primary NIC MAC (at most one address).

    Prefers wired names (``eth*`` / ``en*`` / ``end*``), then Wi-Fi, then the first
    name. Virtual interfaces are skipped. Routes are not used: this MAC identifies
    the device, so it must not change when traffic moves from Ethernet to Wi-Fi.
    """
    net_dir = _sys_class_net()
    try:
        interfaces = [p.name for p in net_dir.iterdir()]
    except OSError:
        return []
    macs: dict[str, str] = {}
    for iface in interfaces:
        if _is_virtual_iface(iface):
            continue
        address = _iface_mac(net_dir, iface)
        if address:
            macs[iface] = address
    if not macs:
        return []
    chosen = min(macs, key=lambda name: (_iface_preference(name), name))
    return [macs[chosen]]


_RTF_UP = 0x1


def _iface_kind(net_dir: Path, iface: str) -> str:
    """ethernet, wifi, cellular or other, from sysfs (no root, no NetworkManager)."""
    base = net_dir / iface
    uevent = _read_text(str(base / "uevent")) or ""
    devtype = next((line.split("=", 1)[1] for line in uevent.splitlines() if line.startswith("DEVTYPE=")), "")
    if devtype == "wlan" or (base / "wireless").exists() or (base / "phy80211").exists():
        return "wifi"
    if devtype == "wwan" or iface.startswith("wwan"):
        return "cellular"
    if _is_virtual_iface(iface) or devtype:
        return "other"
    # ARPHRD_ETHER on real hardware (a "device" link); bridges and tunnels have none.
    if _read_text(str(base / "type")) == "1" and (base / "device").exists():
        return "ethernet"
    return "other"


def read_uplink() -> dict[str, str]:
    """The link this device's traffic leaves on: the IPv4 default route with the lowest metric.

    ``{"uplink_type": "ethernet", "uplink_interface": "eth0"}``, or ``{}`` without a default route.
    """
    content = _read_text("/proc/net/route")
    if not content:
        return {}
    best: tuple[int, str] | None = None
    for line in content.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8 or fields[1] != "00000000" or fields[7] != "00000000":
            continue
        try:
            flags, metric = int(fields[3], 16), int(fields[6])
        except ValueError:
            continue
        if flags & _RTF_UP and (best is None or metric < best[0]):
            best = (metric, fields[0])
    if best is None:
        return {}
    iface = best[1]
    return {"uplink_type": _iface_kind(_sys_class_net(), iface), "uplink_interface": iface[:32]}


def read_cpu() -> tuple[str | None, int | None]:
    """Return (cpu_model, cpu_cores).

    x86 uses ``model name``. Raspberry Pi / Jetson-class ARM boards often only
    expose ``Hardware``, ``Model``, or ``CPU part`` — try those so enrollments
    send the same ``cpu_model`` field on every board family.
    """
    content = _read_text("/proc/cpuinfo")
    model: str | None = None
    hardware: str | None = None
    board_model: str | None = None
    cpu_part: str | None = None
    cores = 0
    if content:
        for line in content.splitlines():
            if ":" not in line:
                continue
            key, _, raw = line.partition(":")
            key = key.strip().lower()
            value = raw.strip()
            if key == "model name" and not model and value:
                model = value
            elif key == "hardware" and not hardware and value:
                hardware = value
            elif key == "model" and not board_model and value:
                board_model = value
            elif key == "cpu part" and not cpu_part and value:
                cpu_part = value
            if key == "processor":
                cores += 1
    if not model:
        model = board_model or hardware or cpu_part
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
        "hardware_model": read_hardware_model(),
        "cpu_model": cpu_model,
        "cpu_cores": cpu_cores,
        "memory_mb": read_memory_mb(),
    }
