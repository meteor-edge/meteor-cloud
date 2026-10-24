"""Allocate stable WireGuard client addresses from a CIDR."""

from __future__ import annotations

import ipaddress

from app.core.exceptions import ConflictError


def allocate_address(*, cidr: str, server_address: str, allocated: set[str]) -> str:
    network = ipaddress.ip_network(cidr, strict=False)
    server_host = ipaddress.ip_address(server_address.split("/", 1)[0])
    used = {ipaddress.ip_address(item.split("/", 1)[0]) for item in allocated}
    used.add(server_host)
    for host in network.hosts():
        if host not in used:
            return str(host)
    raise ConflictError(
        "connectivity_pool_exhausted",
        "No free WireGuard addresses remain in the configured client CIDR.",
    )
