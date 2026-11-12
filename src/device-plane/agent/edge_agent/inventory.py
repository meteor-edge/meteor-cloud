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
    """Read a text or device-tree file. Device-tree values are NUL-terminated."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    text = data.decode("utf-8", errors="ignore").replace("\x00", "").strip()
    return text or None


def _agent_version() -> str | None:
    """Installed meteorcli version (X.Y.Z), or None when the package is missing."""
    try:
        from meteorcli import __version__
    except ImportError:
        return None
    version = str(__version__).strip()
    return version or None


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


def _real_token(value: str | None) -> str | None:
    cleaned = (value or "").replace("\x00", "").strip()
    if cleaned and cleaned.lower() not in _PLACEHOLDER_VALUES and cleaned.strip("0"):
        return cleaned
    return None


def _first_real_value(paths: tuple[str, ...]) -> str | None:
    for path in paths:
        value = _real_token(_read_text(path))
        if value:
            return value
    return None


def _cpuinfo_entries() -> list[tuple[str, str]]:
    content = _read_text("/proc/cpuinfo")
    if not content:
        return []
    entries: list[tuple[str, str]] = []
    for line in content.splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        entries.append((key.strip().lower(), raw.strip()))
    return entries


def read_serial_number() -> str | None:
    """Board serial.

    64-bit Raspberry Pi kernels omit ``Serial`` from ``/proc/cpuinfo``; the
    device tree still has it. PCs expose it through DMI. Placeholder strings
    (all zeros, "To be filled by O.E.M.") are ignored.
    """
    for key, value in _cpuinfo_entries():
        if key == "serial":
            serial = _real_token(value)
            if serial:
                return serial
            break
    return _first_real_value(
        (
            "/sys/firmware/devicetree/base/serial-number",
            "/proc/device-tree/serial-number",
            "/sys/class/dmi/id/product_serial",
            "/sys/class/dmi/id/board_serial",
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


# ARM Ltd MIDR part numbers. 64-bit Pi kernels have no "model name"; they only
# report these codes (Pi 4 is Cortex-A72, Pi 5 is Cortex-A76).
_ARM_CPU_PARTS = {
    "0xd03": "ARM Cortex-A53",
    "0xd04": "ARM Cortex-A35",
    "0xd05": "ARM Cortex-A55",
    "0xd07": "ARM Cortex-A57",
    "0xd08": "ARM Cortex-A72",
    "0xd09": "ARM Cortex-A73",
    "0xd0a": "ARM Cortex-A75",
    "0xd0b": "ARM Cortex-A76",
    "0xd0d": "ARM Cortex-A77",
    "0xd41": "ARM Cortex-A78",
    "0xd42": "ARM Cortex-A78AE",
    "0xd47": "ARM Cortex-A710",
}
_ARM_IMPLEMENTER = "0x41"


def _cpu_part_name(part: str | None, implementer: str | None) -> str | None:
    if not part:
        return None
    if implementer and implementer.lower() != _ARM_IMPLEMENTER:
        return None
    key = part.lower()
    if not key.startswith("0x"):
        key = "0x" + key
    return _ARM_CPU_PARTS.get(key)


def read_cpu() -> tuple[str | None, int | None]:
    """Return (cpu_model, cpu_cores).

    x86 uses ``model name``. Aarch64 boards (Raspberry Pi OS 64-bit) usually
    have only ``CPU part``; known ARM parts are reported as a CPU name such as
    ``ARM Cortex-A76``. ``Model`` / ``Hardware`` are the last resort.
    """
    model: str | None = None
    hardware: str | None = None
    board_model: str | None = None
    cpu_part: str | None = None
    implementer: str | None = None
    cores = 0
    for key, value in _cpuinfo_entries():
        if not value:
            continue
        if key == "model name" and not model:
            model = value
        elif key == "hardware" and not hardware:
            hardware = value
        elif key == "model" and not board_model:
            board_model = value
        elif key == "cpu part" and not cpu_part:
            cpu_part = value
        elif key == "cpu implementer" and not implementer:
            implementer = value
        if key == "processor":
            cores += 1
    if not model:
        model = _cpu_part_name(cpu_part, implementer) or board_model or hardware or cpu_part
    if cores == 0:
        cores = os.cpu_count() or 0
    return model, (cores or None)


# Linux SIOCGIFADDR: IPv4 address of a named interface. Not in the socket module.
_SIOCGIFADDR = 0x8915


def _iface_ipv4(name: str) -> str | None:
    import fcntl
    import struct

    # An immutable buffer is not updated in place: ioctl returns a new one.
    request = struct.pack("256s", name.encode("utf-8")[:15])
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, request)
    except OSError:
        return None
    if not isinstance(packed, (bytes, bytearray)):
        return None
    return socket.inet_ntoa(bytes(packed[20:24]))


def _usable_ipv4(address: str | None) -> str | None:
    if not address or address == "0.0.0.0" or address.startswith("127.") or address.startswith("169.254."):
        return None
    return address


def read_local_ipv4() -> str | None:
    """LAN address of the primary physical interface (wired, then Wi-Fi).

    Loopback, link-local, containers, bridges, and WireGuard are skipped.
    """
    net_dir = _sys_class_net()
    try:
        interfaces = [path.name for path in net_dir.iterdir()]
    except OSError:
        return None
    candidates = [name for name in interfaces if not _is_virtual_iface(name)]
    for iface in sorted(candidates, key=lambda name: (_iface_preference(name), name)):
        address = _usable_ipv4(_iface_ipv4(iface))
        if address:
            return address
    return None


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
        "local_ip": read_local_ipv4(),
        "agent_version": _agent_version(),
    }
