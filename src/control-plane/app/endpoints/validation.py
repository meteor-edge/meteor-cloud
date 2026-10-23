"""Hostname and upstream safety checks for public app endpoints."""

from __future__ import annotations

import ipaddress
import re

from app.core.exceptions import ValidationAppError

_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Custom names that would shadow platform or well-known hosts. Sites add their own with
# APPS_RESERVED_NAMES (e.g. hosts that already exist under the apps domain).
_RESERVED_HOSTS = frozenset(
    {
        "admin", "api", "app", "apps", "auth", "backend", "console", "dashboard", "docs",
        "ftp", "grafana", "help", "localhost", "login", "mail", "metadata", "mqtt", "mx", "ns",
        "ns1", "ns2", "postgres", "redis", "smtp", "sso", "status", "support", "traefik", "vpn",
        "wg", "www",
    }
)
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
)


class HostnameRejected(ValidationAppError):
    """A hostname that can never be used; ``reason`` says why (for the availability check)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__("invalid_hostname", message)
        self.reason = reason


def normalize_domain(apps_base_domain: str) -> str:
    return apps_base_domain.strip().lower().strip(".")


def dns_label(value: str, fallback: str) -> str:
    """A display name as one DNS label: lowercase letters, digits and hyphens, at most 63 chars."""
    label = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:63].strip("-")
    return label or fallback


def suggested_hostname(*, app: str, device_name: str, org_slug: str, apps_base_domain: str) -> str:
    """The default public name of an app: ``<app>.<device>.<org>.<apps domain>``."""
    return ".".join(
        (
            dns_label(app, "app"),
            dns_label(device_name, "device"),
            dns_label(org_slug, "org"),
            normalize_domain(apps_base_domain),
        )
    )


def parse_hostname(hostname: str, *, apps_base_domain: str, reserved: frozenset[str] = frozenset()) -> list[str]:
    """Labels in front of the apps domain: [name] (custom) or [app, device, org] (suggested)."""
    domain = normalize_domain(apps_base_domain)
    value = hostname.strip().lower().rstrip(".")
    if not value.endswith(f".{domain}"):
        raise HostnameRejected("invalid", f"The address must end in .{domain}.")
    labels = value[: -len(domain) - 1].split(".")
    if any(not _LABEL_RE.match(label) for label in labels):
        raise HostnameRejected(
            "invalid",
            "Each part may use letters, digits and hyphens (at most 63, not starting or ending with a hyphen).",
        )
    if len(labels) == 1:
        if labels[0] in _RESERVED_HOSTS or labels[0] in reserved:
            raise HostnameRejected("reserved", f"'{labels[0]}' is reserved. Choose another name.")
        return labels
    if len(labels) == 3:
        return labels
    raise HostnameRejected("invalid", f"Use name.{domain} or app.device.organization.{domain}.")


def validate_hostname(
    hostname: str,
    *,
    apps_base_domain: str,
    org_slug: str,
    device_name: str,
    reserved: frozenset[str] = frozenset(),
) -> str:
    """Normalized hostname, or HostnameRejected. A three-part name must name this device and organization."""
    labels = parse_hostname(hostname, apps_base_domain=apps_base_domain, reserved=reserved)
    if len(labels) == 3:
        org, device = dns_label(org_slug, "org"), dns_label(device_name, "device")
        if labels[2] != org or labels[1] != device:
            raise HostnameRejected(
                "not_yours",
                f"An app.device.organization address must end in .{device}.{org}.{normalize_domain(apps_base_domain)}.",
            )
    return hostname.strip().lower().rstrip(".")


def tls_domain(hostname: str, *, apps_base_domain: str) -> str:
    """Wildcard certificate that covers a hostname: one for custom names, one per device otherwise."""
    domain = normalize_domain(apps_base_domain)
    labels = hostname[: -len(domain) - 1].split(".")
    if len(labels) == 1:
        return f"*.{domain}"
    return "*." + hostname.split(".", 1)[1]


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
