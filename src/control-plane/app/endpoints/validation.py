"""Hostname and upstream safety checks for public app endpoints."""

from __future__ import annotations

import ipaddress
import re

from app.core.exceptions import ValidationAppError

_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_RESERVED_HOSTS = frozenset(
    {
        "localhost",
        "metadata",
        "traefik",
        "backend",
        "console",
        "postgres",
        "redis",
    }
)
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
)


def hostname_suffix(apps_base_domain: str) -> str:
    domain = apps_base_domain.strip().lower().strip(".")
    if domain.startswith("appx."):
        return f".{domain}"
    return f".appx.{domain}"


def validate_hostname(hostname: str, *, apps_base_domain: str) -> str:
    value = hostname.strip().lower().rstrip(".")
    suffix = hostname_suffix(apps_base_domain)
    if not value.endswith(suffix) or value == suffix.lstrip("."):
        raise ValidationAppError(
            "invalid_hostname",
            f"Hostname must be a label under *{suffix} (for example boiler{suffix}).",
        )
    label = value[: -len(suffix)]
    if "." in label or not _LABEL_RE.match(label) or label in _RESERVED_HOSTS:
        raise ValidationAppError(
            "invalid_hostname",
            "Use a single DNS label (letters, digits, hyphen) before the appx suffix.",
        )
    return value


def assert_wireguard_upstream(host: str, *, client_cidr: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValidationAppError("invalid_upstream", "Upstream host must be a WireGuard address.") from exc
    for blocked in _BLOCKED_NETWORKS:
        if address in blocked:
            raise ValidationAppError("invalid_upstream", "Upstream address is not allowed.")
    network = ipaddress.ip_network(client_cidr, strict=False)
    if address not in network:
        raise ValidationAppError("invalid_upstream", "Upstream address is outside the WireGuard client CIDR.")
