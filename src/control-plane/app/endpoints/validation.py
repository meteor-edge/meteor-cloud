"""Hostname and upstream safety checks for public app endpoints."""

from __future__ import annotations

import ipaddress
import re

from app.core.exceptions import ValidationAppError

_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Names directly under the apps domain that would shadow platform or well-known hosts. Sites add
# their own with APPS_RESERVED_NAMES (e.g. hosts that already exist under the apps domain).
_RESERVED_HOSTS = frozenset(
    {
        "admin", "api", "app", "apps", "auth", "backend", "console", "dashboard", "docs",
        "ftp", "grafana", "help", "localhost", "login", "mail", "metadata", "mqtt", "mx", "ns",
        "ns1", "ns2", "postgres", "redis", "smtp", "sso", "status", "support", "traefik", "vpn",
        "wg", "www",
    }
)
_MAX_HOSTNAME_LENGTH = 253
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


def normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


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


def custom_name_domain(*, org_slug: str, apps_base_domain: str, allow_short_names: bool = False) -> str:
    """The domain a custom name goes under: ``<org>.<apps domain>``, or the apps domain if short names are on."""
    domain = normalize_domain(apps_base_domain)
    return domain if allow_short_names else f"{dns_label(org_slug, 'org')}.{domain}"


def parse_hostname(hostname: str, *, apps_base_domain: str, reserved: frozenset[str] = frozenset()) -> list[str]:
    """Labels in front of the apps domain: [name] (short), [name, org] (custom) or [app, device, org] (suggested)."""
    domain = normalize_domain(apps_base_domain)
    value = normalize_hostname(hostname)
    if len(value) > _MAX_HOSTNAME_LENGTH:
        raise HostnameRejected("invalid", f"The address can have at most {_MAX_HOSTNAME_LENGTH} characters.")
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
    if len(labels) in (2, 3):
        return labels
    raise HostnameRejected("invalid", f"Use name.organization.{domain} or app.device.organization.{domain}.")


def validate_hostname(
    hostname: str,
    *,
    apps_base_domain: str,
    org_slug: str,
    device_name: str,
    reserved: frozenset[str] = frozenset(),
    allow_short_names: bool = False,
) -> str:
    """Normalized hostname, or HostnameRejected. An address must name this organization (and device, if it has one)."""
    labels = parse_hostname(hostname, apps_base_domain=apps_base_domain, reserved=reserved)
    domain = normalize_domain(apps_base_domain)
    org = dns_label(org_slug, "org")
    if len(labels) == 1:
        if not allow_short_names:
            raise HostnameRejected("invalid", f"Add your organization: {labels[0]}.{org}.{domain}.")
    elif len(labels) == 2:
        if labels[1] != org:
            raise HostnameRejected("not_yours", f"A custom address must end in .{org}.{domain}.")
        # DNS and platform hosts with this name would hide or shadow everything under it.
        if org in _RESERVED_HOSTS or org in reserved:
            raise HostnameRejected(
                "reserved", f"The organization name '{org}' is reserved, so use the suggested address instead."
            )
    else:
        device = dns_label(device_name, "device")
        if labels[2] != org or labels[1] != device:
            raise HostnameRejected(
                "not_yours", f"An app.device.organization address must end in .{device}.{org}.{domain}."
            )
    return normalize_hostname(hostname)


def tls_domain(hostname: str, *, apps_base_domain: str) -> str:
    """Wildcard certificate that covers a hostname: one for short names, one per organization, or one per device."""
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
