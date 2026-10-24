"""Diagnostics bundle: service logs, tunnel state, and routing, redacted."""

from __future__ import annotations

import time
from pathlib import Path

from e2e.redact import redact
from e2e.ssh import Ssh
from e2e.state import Host, State

COMMON = {
    "journal-ssh.txt": "journalctl -u ssh --no-pager -n 100",
    "system.txt": "uptime; df -h /; free -m; ip -br addr",
}
CONTROL_PLANE = {
    "docker-ps.txt": "docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'",
    "compose-logs.txt": "cd /opt/edge-platform/compose && docker compose logs --no-color --tail 400 2>&1",
    "traefik-routes.json": (
        "docker exec $(docker ps -q -f label=com.docker.compose.service=backend | head -n1) python -c "
        "\"import os,urllib.request as u;print(u.urlopen(u.Request('http://127.0.0.1:8000/internal/routing/traefik',"
        "headers={'X-Routing-Internal-Token':os.environ.get('ROUTING_INTERNAL_TOKEN') or "
        "os.environ['CONNECTIVITY_INTERNAL_TOKEN']}),timeout=10).read().decode())\""
    ),
}
GATEWAY = {
    "wg-show.txt": "wg show all",  # public keys, endpoints, handshakes; never private keys
    "journal-wireguard.txt": "journalctl -u wg-quick@wg0 -u wg-quick@wg-link -u meteorcloud-gateway --no-pager -n 300",
    "iptables.txt": "iptables -S; iptables -t nat -S",
}
DEVICE = {
    "journal-meteorcli.txt": "journalctl -u meteorcli --no-pager -n 400",
    "wg-show.txt": "wg show all",
    "docker-ps.txt": "docker ps -a --format 'table {{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}'",
    "meteorcli-files.txt": "ls -la /etc/meteorcli /etc/meteorcli/wireguard 2>&1",
}


def collect(state: State, ssh: Ssh, hosts: list[Host], *, label: str = "") -> Path:
    target = state.logs_dir / (time.strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else ""))
    target.mkdir(parents=True, exist_ok=True)
    gateway_roles = {"gateway"} if any(h.role == "gateway" for h in hosts) else {"control_plane"}
    for host in hosts:
        commands = dict(COMMON)
        if host.role == "control_plane":
            commands.update(CONTROL_PLANE)
        if host.role in gateway_roles:
            commands.update(GATEWAY)
        if host.role == "device":
            commands.update(DEVICE)
        directory = target / host.name
        directory.mkdir(exist_ok=True)
        for name, command in commands.items():
            result = ssh.run(host, command, sudo=True, timeout=120)
            (directory / name).write_text(redact(result.stdout + result.stderr), encoding="utf-8")
    return target
