"""Default device names, so nobody has to invent one to enroll a device.

The name is a starting point: administrators can change it when approving or later.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence

_MAX_LENGTH = 255
_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]")
# OS image defaults: shared by every device installed from that image.
_GENERIC_HOSTNAMES = frozenset(
    {
        "alpine",
        "archlinux",
        "armbian",
        "centos",
        "debian",
        "dietpi",
        "fedora",
        "jetson",
        "nixos",
        "orangepi",
        "raspberrypi",
        "rocky",
        "ubuntu",
    }
)
_MEANINGLESS_HOSTNAMES = frozenset({"(none)", "localhost"})


def _hardware_suffix(*, serial_number: str | None, mac_addresses: Sequence[str]) -> str:
    """The end of the primary MAC (or the serial, which is often a placeholder on x86 boards)."""
    source = mac_addresses[0] if mac_addresses else serial_number or ""
    return _NOT_ALPHANUMERIC.sub("", source.lower())[-4:]


def default_device_name(
    *,
    requested_name: str | None,
    hostname: str | None,
    serial_number: str | None,
    mac_addresses: Sequence[str],
) -> str:
    """The device's own name, a specific hostname, or a generic one made distinct by its hardware."""
    if requested_name and requested_name.strip():
        return requested_name.strip()[:_MAX_LENGTH]
    host = (hostname or "").strip()
    label = host.split(".", 1)[0].lower()
    if label and label not in _GENERIC_HOSTNAMES and label not in _MEANINGLESS_HOSTNAMES:
        return host[:_MAX_LENGTH]
    base = label if label in _GENERIC_HOSTNAMES else "device"
    suffix = _hardware_suffix(serial_number=serial_number, mac_addresses=mac_addresses)
    return f"{base}-{suffix}" if suffix else base


def unique_device_name(name: str, *, taken: Collection[str]) -> str:
    """``name``, or ``name-2``, ``name-3``… if that is taken. ``taken`` holds casefolded names."""
    if name.casefold() not in taken:
        return name
    number = 2
    while True:
        ending = f"-{number}"
        candidate = f"{name[: _MAX_LENGTH - len(ending)]}{ending}"
        if candidate.casefold() not in taken:
            return candidate
        number += 1
