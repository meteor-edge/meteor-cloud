"""Default device names, so nobody has to invent one to enroll a device.

A default name is ``<kind>-<hardware suffix>``, for example ``raspberrypi5-a1b2c3``: the
kind of device, then the last three bytes of its MAC address. The name is a starting
point: administrators can change it when approving or later.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from typing import Protocol

_MAX_LENGTH = 255
_MAX_KIND_LENGTH = 40
_SUFFIX_LENGTH = 6
_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_MEANINGLESS_HOSTNAMES = frozenset({"(none)", "localhost"})
# Firmware placeholders that say nothing about the board.
_MEANINGLESS_MODELS = frozenset(
    {
        "default string",
        "none",
        "not applicable",
        "not specified",
        "o.e.m.",
        "oem",
        "system product name",
        "to be filled by o.e.m.",
        "unknown",
    }
)
# "Raspberry Pi 5 Model B Rev 1.0" -> "raspberrypi5": the revision and board variant say too much.
_MODEL_NOISE = (
    re.compile(r"\brev(ision)?\b.*$"),
    re.compile(r"\bmodel [a-z]\b( plus\b)?"),
    re.compile(r"\b(developer|dev|development) kit\b"),
)


class DeviceTypeLike(Protocol):
    name: str
    slug: str
    manufacturer: str | None
    model: str | None


def _compact(value: str) -> str:
    return _NOT_ALPHANUMERIC.sub("", value.lower())


def _tokens(value: str) -> list[str]:
    return [token for token in _NOT_ALPHANUMERIC.split(value.lower()) if token]


def meaningful_model(hardware_model: str | None) -> str | None:
    """The reported board model, or None for firmware placeholders."""
    model = (hardware_model or "").replace("\x00", "").strip()
    if not model or model.lower() in _MEANINGLESS_MODELS:
        return None
    return model


def model_kind(hardware_model: str | None) -> str | None:
    """A short kind from a board model: ``Raspberry Pi 5 Model B Rev 1.0`` -> ``raspberrypi5``."""
    model = meaningful_model(hardware_model)
    if model is None:
        return None
    text = model.lower().replace("compute module", "cm")
    for noise in _MODEL_NOISE:
        text = noise.sub(" ", text)
    return _compact(text)[:_MAX_KIND_LENGTH] or None


def type_kind(slug: str | None) -> str | None:
    """A device type's slug without separators: ``raspberry-pi-5`` -> ``raspberrypi5``."""
    return _compact(slug or "")[:_MAX_KIND_LENGTH] or None


def match_device_type[T: DeviceTypeLike](hardware_model: str | None, device_types: Iterable[T]) -> T | None:
    """The catalog device type this board model belongs to, if exactly one fits best.

    A type fits when its name, its model, or manufacturer + model are the leading words of
    the reported model, so ``Raspberry Pi 4`` fits ``Raspberry Pi 4 Model B`` but not
    ``Raspberry Pi 400``. The most specific fit wins.
    """
    model = meaningful_model(hardware_model)
    if model is None:
        return None
    reported = _tokens(model)
    best: T | None = None
    best_length = 0
    tied = False
    for device_type in device_types:
        candidates = [device_type.name, device_type.model or ""]
        if device_type.manufacturer and device_type.model:
            candidates.append(f"{device_type.manufacturer} {device_type.model}")
        length = max(
            (len(words) for words in map(_tokens, candidates) if words and reported[: len(words)] == words),
            default=0,
        )
        if length > best_length:
            best, best_length, tied = device_type, length, False
        elif length and length == best_length:
            tied = True
    return None if tied else best


def device_type_slug_for[T: DeviceTypeLike](
    device_type_id: object | None, hardware_model: str | None, device_types: Sequence[T]
) -> str | None:
    """Slug of the assigned device type, else of the catalog type that fits the board model."""
    if device_type_id is not None:
        for device_type in device_types:
            if getattr(device_type, "id", None) == device_type_id:
                return device_type.slug
    matched = match_device_type(hardware_model, device_types)
    return matched.slug if matched is not None else None


def hardware_suffix(*, serial_number: str | None, mac_addresses: Sequence[str]) -> str:
    """The last three bytes of the primary MAC (or the end of the serial without one)."""
    source = mac_addresses[0] if mac_addresses else serial_number or ""
    return _compact(source)[-_SUFFIX_LENGTH:]


def chosen_name(requested_name: str | None, hostname: str | None) -> str | None:
    """The name someone picked for this device, or None.

    Agents up to 0.2.0 ask for their hostname when nobody picked a name, so a requested
    name equal to the hostname is not a choice.
    """
    name = (requested_name or "").strip()
    if not name:
        return None
    host = (hostname or "").strip().casefold()
    if host and name.casefold() in {host, host.split(".", 1)[0]}:
        return None
    return name[:_MAX_LENGTH]


def _hostname_kind(hostname: str | None) -> str | None:
    label = (hostname or "").strip().split(".", 1)[0].lower()
    if not label or label in _MEANINGLESS_HOSTNAMES:
        return None
    return _NOT_ALPHANUMERIC.sub("-", label).strip("-")[:_MAX_KIND_LENGTH] or None


def default_device_name(
    *,
    requested_name: str | None,
    hostname: str | None,
    serial_number: str | None,
    mac_addresses: Sequence[str],
    device_type_slug: str | None = None,
    hardware_model: str | None = None,
) -> str:
    """A chosen name, or ``<kind>-<suffix>``.

    The kind is the device type, else the board model, else the hostname, else ``device``.
    """
    chosen = chosen_name(requested_name, hostname)
    if chosen:
        return chosen
    kind = type_kind(device_type_slug) or model_kind(hardware_model) or _hostname_kind(hostname) or "device"
    suffix = hardware_suffix(serial_number=serial_number, mac_addresses=mac_addresses)
    return f"{kind}-{suffix}" if suffix else kind


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
