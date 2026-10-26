"""Stage 1: hosts, configuration management, and network exposure."""

from __future__ import annotations

from e2e.http import tcp_open
from e2e.suite.framework import Blocked, check, expect

STAGE = "1 infrastructure"
# Must never answer from the internet: Postgres, Redis, API, MinIO API/console, Traefik dashboard, Grafana, Prometheus.
INTERNAL_PORTS = (5432, 6379, 8000, 9000, 9001, 8080, 3000, 9090)


@check("infra.ssh", STAGE, "Every host is reachable over SSH with a pinned host key")
def ssh_reachable(ctx):
    seen = {}
    for host in ctx.hosts:
        result = ctx.sh(host, "hostname && . /etc/os-release && echo $PRETTY_NAME", sudo=False, timeout=60)
        seen[host.name] = result.stdout.strip().replace("\n", " / ")
    return {"hosts": seen}


@check("infra.ansible_idempotent", STAGE, "A second Ansible run changes nothing", needs=("infra.ssh",))
def ansible_idempotent(ctx):
    runs = ctx.state.load().get("ansible", {})
    second = runs.get("second_run")
    if not second:
        raise Blocked("no recorded second Ansible run (run `meteorcloud-testenv up`)")
    changed = {host: r for host, r in second.items() if r["changed"] or r["failed"] or r["unreachable"]}
    expect(not changed, f"second run changed or failed on: {changed}")
    return {"second_run": second}


@check("infra.no_world_ssh", STAGE, "No security group allows SSH from 0.0.0.0/0",
       when=lambda ctx: ctx.cfg.provider == "aws")
def no_world_ssh(ctx):
    from e2e.terraform import Terraform

    groups = [r for r in Terraform(ctx.cfg, ctx.state).resources() if r["type"] == "aws_security_group"]
    expect(groups, "no security groups found in this environment's Terraform state")
    offenders, ssh_rules = [], {}
    for group in groups:
        for rule in group["attributes"].get("ingress", []):
            if rule["from_port"] <= 22 <= rule["to_port"] or rule["protocol"] == "-1":
                cidrs = list(rule.get("cidr_blocks") or []) + list(rule.get("ipv6_cidr_blocks") or [])
                ssh_rules[group["attributes"]["name"]] = cidrs
                if any(c.endswith("/0") for c in cidrs):
                    offenders.append(group["attributes"]["name"])
    expect(not offenders, f"world-open SSH in {offenders}")
    return {"ssh_rules": ssh_rules}


@check("infra.internal_ports_closed", STAGE, "Databases, MinIO and admin ports are closed from the internet")
def internal_ports_closed(ctx):
    targets = {h.name: h.address for h in ctx.hosts if h.role in ("control_plane", "gateway")}
    open_ports = {name: [p for p in INTERNAL_PORTS if tcp_open(address, p)] for name, address in targets.items()}
    expect(not any(open_ports.values()), f"reachable from the runner: {open_ports}")
    return {"probed": {n: list(INTERNAL_PORTS) for n in targets}, "open": open_ports}


@check("infra.stack_healthy", STAGE, "Control-plane containers are running and healthy", needs=("infra.ssh",))
def stack_healthy(ctx):
    result = ctx.sh(ctx.cp, "docker ps --filter label=com.docker.compose.project=meteorcloud "
                            "--format '{{.Label \"com.docker.compose.service\"}}\t{{.Status}}'")
    services = dict(line.split("\t", 1) for line in result.stdout.strip().splitlines() if "\t" in line)
    for required in ("backend", "console", "traefik", "postgres", "minio"):
        expect(required in services, f"service {required} is not running (running: {sorted(services)})")
    unhealthy = {s: st for s, st in services.items() if "unhealthy" in st or not st.startswith("Up")}
    expect(not unhealthy, f"unhealthy services: {unhealthy}")
    return {"services": services}
