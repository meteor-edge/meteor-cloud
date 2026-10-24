"""Inventory generation and playbook runs (deploy/ansible, the product's own automation)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from e2e.config import REPO_ROOT, Config
from e2e.proc import run_logged
from e2e.state import Host, State

ANSIBLE_DIR = REPO_ROOT / "deploy" / "ansible"
RECAP_RE = re.compile(r"^(?P<host>\S+)\s+:\s+ok=(?P<ok>\d+)\s+changed=(?P<changed>\d+)\s+unreachable=(?P<unreachable>\d+)"
                      r"\s+failed=(?P<failed>\d+)", re.MULTILINE)


def platform_vars(cfg: Config, *, platform_archive: Path, gateway: Host | None, control_plane: Host) -> dict[str, Any]:
    public_url = f"https://{cfg.fqdn}" if cfg.has_public_dns else f"http://{control_plane.address}"
    values: dict[str, Any] = {
        "enabled_services": ["cloud_app", "vpn"],
        "image_source": "archive",
        "platform_source_archive": str(platform_archive),
        "image_pull_policy": "never",
        "platform_domain": cfg.fqdn if cfg.has_public_dns else "",
        "platform_public_url": public_url,
        "apps_base_domain": cfg.apps_base_domain,
        "registration_require_https": cfg.has_public_dns,
        # Bundled MinIO for artifacts; it is never published on the host.
        "object_storage_provider": "s3",
        "minio_enabled": True,
        "shell_provider": "ssh_gateway",
        # Faster convergence for the suite (product defaults are 60s / 150s).
        "device_heartbeat_interval_seconds": 15,
        "device_offline_threshold_seconds": 45,
    }
    if cfg.dns_provider == "route53" and cfg.provider == "aws":
        values["traefik_aws_region"] = cfg.aws_region
    if gateway is not None and gateway.private_ip and cfg.provider == "aws":
        values["vpn_link_endpoint"] = f"{gateway.private_ip}:51820"
    return values


def render_inventory(cfg: Config, state: State, hosts: list[Host], *, platform_archive: Path,
                     device_archive: Path, ssh_key: Path) -> dict[str, Any]:
    control_plane = next(h for h in hosts if h.role == "control_plane")
    gateway = next((h for h in hosts if h.role == "gateway"), None)
    devices = [h for h in hosts if h.role == "device"]

    def entry(host: Host, **extra: Any) -> dict[str, Any]:
        return {"ansible_host": host.address, "ansible_user": host.user, **extra}

    # Devices dial wg.<fqdn> (a DNS name they can follow) when there is public DNS, else the IP.
    wireguard_host = gateway or control_plane
    wireguard_name = {"vpn_public_host": f"wg.{cfg.fqdn}" if cfg.has_public_dns else wireguard_host.address}

    inventory: dict[str, Any] = {
        "all": {
            "vars": {
                "ansible_ssh_private_key_file": str(ssh_key),
                # Host keys are pinned per environment (trust on first use), never ignored.
                "ansible_ssh_common_args": (
                    f"-o UserKnownHostsFile={state.known_hosts} -o StrictHostKeyChecking=accept-new "
                    "-o IdentitiesOnly=yes"
                ),
            },
            "children": {
                "platform": {
                    "hosts": {"control-plane": entry(control_plane, **({} if gateway else wireguard_name))},
                    "vars": platform_vars(cfg, platform_archive=platform_archive, gateway=gateway,
                                          control_plane=control_plane),
                },
                "devices": {
                    "hosts": {h.name: entry(h) for h in devices},
                    "vars": {"device_source_archive": str(device_archive)},
                },
            },
        }
    }
    if gateway is not None:
        inventory["all"]["children"]["vpn_gateway"] = {
            "hosts": {"vpn-gateway": entry(gateway, **wireguard_name)}
        }
    state.inventory.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    os.chmod(state.inventory, 0o600)
    return inventory


class Ansible:
    def __init__(self, cfg: Config, state: State, secrets: dict[str, str]) -> None:
        self.cfg = cfg
        self.state = state
        self.secrets = secrets

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.secrets)
        env.update(
            ANSIBLE_CONFIG=str(ANSIBLE_DIR / "ansible.cfg"),
            # The repository config turns host key checking off; the suite turns it back on.
            ANSIBLE_HOST_KEY_CHECKING="True",
            ANSIBLE_FORCE_COLOR="0",
            ANSIBLE_NOCOLOR="1",
            ANSIBLE_RETRY_FILES_ENABLED="0",
        )
        return env

    def playbook(self, name: str, *, label: str, limit: str = "") -> dict[str, dict[str, int]]:
        args = ["ansible-playbook", "-i", str(self.state.inventory), f"playbooks/{name}"]
        if limit:
            args += ["--limit", limit]
        log = self.state.logs_dir / f"ansible-{label}.log"
        print(f"    ansible {name} ({label}) -> {log}", flush=True)
        _, output = run_logged(args, log=log, env=self._env(), cwd=ANSIBLE_DIR)
        return parse_recap(output)


def parse_recap(output: str) -> dict[str, dict[str, int]]:
    recap: dict[str, dict[str, int]] = {}
    tail = output.rsplit("PLAY RECAP", 1)[-1] if "PLAY RECAP" in output else ""
    for match in RECAP_RE.finditer(tail):
        recap[match["host"]] = {k: int(match[k]) for k in ("ok", "changed", "unreachable", "failed")}
    return recap
