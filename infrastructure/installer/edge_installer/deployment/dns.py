"""The DNS records an installation needs, for people to create in their DNS zone."""

from __future__ import annotations

from edge_installer.config.models import InstallationConfig
from edge_installer.providers.aws.outputs import TerraformOutputs


def wireguard_name(domain: str) -> str:
    """The name devices dial for WireGuard: a name, so they follow the server to a new IP."""
    return f"wg.{domain}"


def dns_records(config: InstallationConfig, outputs: TerraformOutputs) -> list[tuple[str, str, str]]:
    """(name, IP, purpose) for each A record; empty without a domain (devices use the IP)."""
    domain = config.platform.domain or ""
    if not domain:
        return []
    ip = outputs.public_ip
    records = [
        (domain, ip, "console, API (/api), MQTT (TCP 8883)"),
        (f"*.{domain}", ip, "public app URLs"),
    ]
    if "vpn" in config.enabled_service_names():
        port = config.services.vpn.listen_port
        records.append((wireguard_name(domain), outputs.vpn_gateway_public_ip or ip,
                        f"WireGuard: devices connect here (UDP {port})"))
    return records
