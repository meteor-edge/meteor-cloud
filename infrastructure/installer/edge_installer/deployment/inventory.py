"""Ansible inventory generation."""

from __future__ import annotations

from pathlib import Path

from edge_installer.config.models import InstallationConfig
from edge_installer.providers.aws.outputs import TerraformOutputs


def write_inventory(
    *,
    path: Path,
    outputs: TerraformOutputs,
    config: InstallationConfig,
) -> None:
    if config.aws is None:
        raise ValueError("aws settings are required to write Ansible inventory")
    key_path = Path(config.aws.ssh_private_key_path).expanduser()
    connection = (
        f"ansible_user={outputs.ssh_username} "
        f"ansible_ssh_private_key_file={key_path} "
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'"
    )
    content = f"[platform]\nedge ansible_host={outputs.connect_ip} {connection}\n"
    # A `vpn_gateway` group makes the Ansible vpn role run WireGuard there and
    # join the platform host to it over a site link.
    if outputs.vpn_gateway_public_ip:
        content += (
            "\n[vpn_gateway]\n"
            f"vpn-gateway ansible_host={outputs.vpn_gateway_public_ip} "
            f"vpn_public_host={outputs.vpn_gateway_public_ip} {connection}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
