"""Device identity normalization and duplicate detection.

Duplicate detection uses hardware serial and overlapping normalized MAC
addresses. Device ID is the platform identifier and is not a hardware signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.devices.models import Device

_MAC_CLEAN = re.compile(r"[^0-9a-f]")


def normalize_mac(value: str) -> str | None:
    """Return a normalized MAC address (lowercase, colon-separated) or None.

    Non-hex characters are stripped; a value is only accepted when exactly 12
    hex digits remain. All-zero MACs are ignored as they are not identifying.
    """
    if not value:
        return None
    cleaned = _MAC_CLEAN.sub("", value.strip().lower())
    if len(cleaned) != 12:
        return None
    if cleaned == "000000000000":
        return None
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


def normalize_macs(values: list[str] | None) -> list[str]:
    """Normalize and de-duplicate a list of MAC addresses, preserving order."""
    if not values:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        normalized = normalize_mac(raw)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@dataclass
class DeviceIdentity:
    """Normalized identifying signals extracted from a registration request."""

    serial_number: str | None = None
    mac_addresses: list[str] = field(default_factory=list)

    @classmethod
    def from_inventory(
        cls,
        *,
        serial_number: str | None,
        mac_addresses: list[str] | None,
    ) -> DeviceIdentity:
        return cls(
            serial_number=_clean(serial_number),
            mac_addresses=normalize_macs(mac_addresses),
        )

    def matches(self, device: Device) -> bool:
        """Return whether this identity refers to the same physical device."""
        if self.serial_number and device.serial_number and self.serial_number == device.serial_number:
            return True
        if self.mac_addresses and device.mac_addresses:
            if set(self.mac_addresses) & set(device.mac_addresses):
                return True
        return False


@dataclass(frozen=True)
class MatchResult:
    """Outcome of matching an identity against existing devices."""

    device: Device | None
    ambiguous: bool
    cross_organization: bool


def match_existing_device(
    identity: DeviceIdentity,
    *,
    organization_id,
    candidates: list[Device],
) -> MatchResult:
    """Match an incoming identity against known devices.

    - A single match in the same organization returns that device.
    - Any match in another organization is reported as ``cross_organization``.
    - Multiple distinct matches in the same organization are ``ambiguous``.
    """
    same_org: list[Device] = []
    for device in candidates:
        if not identity.matches(device):
            continue
        if device.organization_id != organization_id:
            return MatchResult(device=None, ambiguous=False, cross_organization=True)
        same_org.append(device)

    if not same_org:
        return MatchResult(device=None, ambiguous=False, cross_organization=False)

    unique_ids = {device.id for device in same_org}
    if len(unique_ids) > 1:
        return MatchResult(device=None, ambiguous=True, cross_organization=False)

    return MatchResult(device=same_org[0], ambiguous=False, cross_organization=False)
