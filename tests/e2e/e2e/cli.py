"""meteorcloud-testenv: validate | up | seed | test | status | logs | down."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from e2e import config as config_mod
from e2e.ansible import Ansible, render_inventory
from e2e.config import Config, ConfigError
from e2e.http import request
from e2e.logs import collect
from e2e.proc import CommandError
from e2e.redact import redact
from e2e.report import Report
from e2e.source import build_archive
from e2e.ssh import Ssh
from e2e.state import Host, State
from e2e.terraform import Terraform

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_INFRA = 0, 1, 2, 3


def say(message: str) -> None:
    print(redact(message), flush=True)


# -- hosts -------------------------------------------------------------------------

def hosts_from_outputs(outputs: dict) -> list[Host]:
    user = outputs.get("ssh_user", "ubuntu")
    cp = outputs["control_plane"]
    hosts = [Host("control-plane", "control_plane", cp["public_ip"], user, cp["private_ip"], cp["instance_id"])]
    if outputs.get("gateway"):
        gw = outputs["gateway"]
        hosts.append(Host("vpn-gateway", "gateway", gw["public_ip"], user, gw["private_ip"], gw["instance_id"]))
    for index, device in enumerate(outputs.get("devices") or [], 1):
        hosts.append(Host(f"device-{index}", "device", device["public_ip"], user, device["private_ip"],
                          device["instance_id"]))
    return hosts


def hosts_from_onprem(cfg: Config) -> list[Host]:
    assert cfg.onprem_control_plane is not None
    cp = cfg.onprem_control_plane
    hosts = [Host("control-plane", "control_plane", cp.address, cp.user, cp.private_ip)]
    if cfg.onprem_gateway:
        gw = cfg.onprem_gateway
        hosts.append(Host("vpn-gateway", "gateway", gw.address, gw.user, gw.private_ip))
    hosts += [Host(d.name, "device", d.address, d.user, d.private_ip) for d in cfg.onprem_devices]
    return hosts


def forget_replaced_hosts(state: State, previous: list[Host], current: list[Host]) -> list[str]:
    """Drop pinned host keys only for machines Terraform replaced (new instance id)."""
    old = {h.name: h for h in previous}
    forgotten = []
    for host in current:
        before = old.get(host.name)
        if before and before.instance_id and before.instance_id != host.instance_id and state.known_hosts.is_file():
            for address in {before.address, host.address}:
                subprocess.run(["ssh-keygen", "-q", "-R", address, "-f", str(state.known_hosts)],
                               capture_output=True, check=False)
            forgotten.append(host.name)
    return forgotten


def wait_ssh(ssh: Ssh, hosts: list[Host], timeout: float = 420) -> None:
    deadline = time.monotonic() + timeout
    pending = list(hosts)
    while pending:
        still = []
        for host in pending:
            result = ssh.run(host, "cloud-init status --wait >/dev/null 2>&1 || true; true", timeout=300)
            if "HOST IDENTIFICATION HAS CHANGED" in result.stderr:
                raise CommandError(["ssh"], 255, f"{host.name}: host key changed for the same instance; refusing")
            if not result.ok:
                still.append(host)
        pending = still
        if not pending:
            return
        if time.monotonic() > deadline:
            raise CommandError(["ssh"], 255, "SSH not reachable: " + ", ".join(h.name for h in pending))
        time.sleep(10)


# -- commands ----------------------------------------------------------------------

def cmd_validate(cfg: Config, state: State, args: argparse.Namespace) -> int:
    errors, warnings = config_mod.check_prerequisites(cfg)
    say(f"environment: {cfg.env_name}  provider={cfg.provider}  vpn={cfg.vpn_placement}  devices={cfg.device_count}")
    say(f"domain: {cfg.fqdn or '(none)'}  dns={cfg.dns_provider}  apps=<name>|<app>.<device>.<org>.{cfg.apps_base_domain}")
    for warning in warnings:
        say(f"warning: {warning}")
    for error in errors:
        say(f"error: {error}")
    if errors:
        return EXIT_CONFIG
    say("validate: OK")
    return EXIT_OK


def provision(cfg: Config, state: State) -> list[Host]:
    key = state.ensure_ssh_key(cfg)
    tf = Terraform(cfg, state)
    if cfg.provider == "aws":
        cidrs = cfg.allowed_ssh_cidrs or [config_mod.detect_public_cidr()]
        say(f"[terraform] provisioning (SSH allowed from {', '.join(cidrs)}); log: {tf.log}")
        tf.write_vars(Path(f"{key}.pub").read_text(encoding="utf-8").strip(), cidrs)
        tf.init()
        tf.apply()
        outputs = tf.outputs()
        hosts = hosts_from_outputs(outputs)
        replaced = forget_replaced_hosts(state, state.hosts(), hosts)
        if replaced:
            say(f"[ssh] new instances (host keys re-pinned): {', '.join(replaced)}")
        state.update(outputs=outputs, allowed_ssh_cidrs=cidrs)
    else:
        hosts = hosts_from_onprem(cfg)
        if cfg.dns_provider == "route53":
            say("[terraform] DNS records for the on-prem ingress")
            tf.write_vars("", [])
            tf.init()
            tf.apply()
    state.update(hosts=[h.__dict__ for h in hosts], provider=cfg.provider, fqdn=cfg.fqdn)
    if cfg.dns_provider == "manual":
        say_dns_records(cfg, hosts)
    return hosts


def dns_records(cfg: Config, hosts: list[Host]) -> list[tuple[str, str, str]]:
    """(name, IP, purpose) for every DNS record the environment needs."""
    by_role = {h.role: h for h in hosts}
    ingress = (cfg.onprem_ingress_ip if cfg.provider == "onprem" else "") or by_role["control_plane"].address
    wireguard = by_role["gateway"].address if cfg.provider == "aws" and "gateway" in by_role else ingress
    return [
        (cfg.fqdn, ingress, "console, API (/api), MQTT (TCP 8883)"),
        (f"*.{cfg.fqdn}", ingress, "public app URLs"),
        (f"wg.{cfg.fqdn}", wireguard, "WireGuard: devices connect here (UDP 51820)"),
    ]


def say_dns_records(cfg: Config, hosts: list[Host]) -> None:
    if not cfg.has_public_dns:
        return
    if cfg.dns_provider == "route53":
        say("  DNS records    created by Terraform in Route53:")
    else:
        say("  DNS records    create these A records yourself (DNS only, no proxy; TTL 60):")
    for name, ip, purpose in dns_records(cfg, hosts):
        say(f"                 {name:<32} -> {ip:<16} {purpose}")


def configure(cfg: Config, state: State, hosts: list[Host], secrets: dict[str, str]) -> None:
    key = state.ensure_ssh_key(cfg)
    platform_archive = build_archive(state.build_dir / "platform-source.tar.gz")
    device_archive = build_archive(state.build_dir / "device-plane.tar.gz", subdir="src/device-plane")
    render_inventory(cfg, state, hosts, platform_archive=platform_archive, device_archive=device_archive,
                     ssh_key=key)
    ansible = Ansible(cfg, state, secrets)
    say("[ansible] configuring control plane, gateway and devices")
    first = ansible.playbook("site.yml", label="site-1")
    first.update(ansible.playbook("device.yml", label="device-1"))
    say("[ansible] second run (must change nothing)")
    second = ansible.playbook("site.yml", label="site-2")
    second.update(ansible.playbook("device.yml", label="device-2"))
    state.update(ansible={"first_run": first, "second_run": second, "at": time.time()})


def run_suite(cfg: Config, state: State, secrets: dict[str, str], *, only: set[str]) -> Report:
    from e2e.suite import (  # noqa: F401 - importing registers the checks
        s1_infra,
        s2_dns_tls,
        s3_artifacts,
        s4_onboarding,
        s5_wireguard,
        s6_shell,
        s7_apps,
        s8_update,
        s9_fleet,
        s10_cleanup,
    )
    from e2e.suite.context import Context, new_run_id
    from e2e.suite.framework import REGISTRY, Runner

    ctx = Context(cfg=cfg, state=state, ssh=Ssh(state.ensure_ssh_key(cfg), state.known_hosts),
                  secrets=secrets, run_id=new_run_id(), hosts=state.hosts())
    report = Report(env_name=cfg.env_name, run_id=ctx.run_id, meta={
        "provider": cfg.provider, "vpn_placement": cfg.vpn_placement, "devices": len(ctx.devices),
        "fqdn": cfg.fqdn, "api_url": ctx.api_url,
        "hosts": {h.name: {"role": h.role, "address": h.address} for h in ctx.hosts},
    })
    say(f"[suite] run {ctx.run_id} against {ctx.api_url}")
    Runner(ctx, report, only=only).run(REGISTRY)
    run_dir = state.reports_dir / ctx.run_id
    json_path, text_path = report.write(run_dir)
    report.write(state.dir)  # latest
    say("\n" + report.summary())
    say(f"\nreport: {json_path}\nsummary: {text_path}")
    return report


def cmd_up(cfg: Config, state: State, args: argparse.Namespace) -> int:
    code = cmd_validate(cfg, state, args)
    if code:
        return code
    state.ensure()
    secrets = state.secrets(cfg)
    hosts: list[Host] = state.hosts()
    try:
        hosts = provision(cfg, state)
        ssh = Ssh(state.ensure_ssh_key(cfg), state.known_hosts)
        say("[ssh] waiting for hosts")
        wait_ssh(ssh, hosts)
        configure(cfg, state, hosts, secrets)
    except CommandError as exc:
        say(f"error: {exc}\n{exc.tail[-3000:]}")
        _after_failure(cfg, state, hosts, args, label="up")
        return EXIT_INFRA
    if args.no_tests:
        say("up: environment ready (tests skipped). Next: meteorcloud-testenv seed")
        return EXIT_OK
    report = run_suite(cfg, state, secrets, only=set(args.only or []))
    if not report.ok:
        _after_failure(cfg, state, hosts, args, label="test")
        return EXIT_FAILED
    return EXIT_OK


def cmd_test(cfg: Config, state: State, args: argparse.Namespace) -> int:
    if not state.exists:
        say("error: no environment state; run `meteorcloud-testenv up` first")
        return EXIT_CONFIG
    report = run_suite(cfg, state, state.secrets(cfg), only=set(args.only or []))
    if not report.ok:
        _after_failure(cfg, state, state.hosts(), args, label="test")
        return EXIT_FAILED
    return EXIT_OK


def cmd_seed(cfg: Config, state: State, args: argparse.Namespace) -> int:
    from e2e.seed import seed

    if not state.exists or not state.hosts():
        say("error: no environment; run `meteorcloud-testenv up --no-tests` first")
        return EXIT_CONFIG
    try:
        return seed(cfg, state, state.secrets(cfg), say)
    except Exception as exc:  # noqa: BLE001 - report any API or SSH error with its message
        say(f"seed failed: {exc}")
        return EXIT_FAILED


def _after_failure(cfg: Config, state: State, hosts: list[Host], args: argparse.Namespace, *, label: str) -> None:
    if hosts:
        try:
            path = collect(state, Ssh(state.ensure_ssh_key(cfg), state.known_hosts), hosts, label=label)
            say(f"diagnostics: {path}")
        except Exception as exc:  # noqa: BLE001 - diagnostics are best effort
            say(f"warning: could not collect diagnostics: {exc}")
    if getattr(args, "destroy_on_failure", False):
        say("--destroy-on-failure: destroying the environment")
        cmd_down(cfg, state, argparse.Namespace(yes=True))
    else:
        say("The environment is kept for debugging. Remove it with: meteorcloud-testenv down")


def cmd_status(cfg: Config, state: State, args: argparse.Namespace) -> int:
    if not state.exists:
        say(f"{cfg.env_name}: no environment")
        return EXIT_FAILED
    data = state.load()
    say(f"environment {cfg.env_name} ({data.get('provider')}), state in {state.dir}")
    for host in state.hosts():
        say(f"  {host.role:<14} {host.name:<14} {host.address:<16} ssh -i {state.ensure_ssh_key(cfg)} "
            f"-o UserKnownHostsFile={state.known_hosts} {host.user}@{host.address}")
    cp = state.host("control_plane")
    url = f"https://{cfg.fqdn}" if cfg.has_public_dns else f"http://{cp.address}" if cp else ""
    if url:
        try:
            response = request("GET", f"{url}/health", ca_file=cfg.ca_file, timeout=10)
            say(f"  console/API    {url}  (/health -> {response.status})")
        except OSError as exc:
            say(f"  console/API    {url}  (unreachable: {exc})")
    say_dns_records(cfg, state.hosts())
    say(f"  admin login    {state.secrets_file} (EDGE_PLATFORM_ADMIN_EMAIL / _PASSWORD)")
    demo_login = state.dir / "demo-login.env"
    if demo_login.is_file():
        say(f"  demo login     {demo_login} (DEMO_EMAIL / DEMO_PASSWORD)")
    latest = state.dir / "report.json"
    if latest.is_file():
        report = json.loads(latest.read_text(encoding="utf-8"))
        say(f"  last run       {report['run_id']}: {'PASS' if report['ok'] else 'FAIL'} {report['counts']}")
    return EXIT_OK


def cmd_logs(cfg: Config, state: State, args: argparse.Namespace) -> int:
    if not state.exists:
        say("error: no environment state")
        return EXIT_CONFIG
    path = collect(state, Ssh(state.ensure_ssh_key(cfg), state.known_hosts), state.hosts(), label="manual")
    say(f"logs: {path}")
    return EXIT_OK


def cmd_down(cfg: Config, state: State, args: argparse.Namespace) -> int:
    if not state.dir.is_dir():
        say(f"{cfg.env_name}: nothing to remove")
        return EXIT_OK
    if not args.yes:
        answer = input(f"Destroy every resource of E2E environment '{cfg.env_name}'? Type the name to confirm: ")
        if answer.strip() != cfg.env_name:
            say("aborted")
            return EXIT_FAILED
    tf = Terraform(cfg, state)
    if cfg.provider == "onprem":
        hosts = state.hosts()
        if hosts:
            say("[ansible] removing the stack from the on-prem control plane (data directories are kept)")
            Ansible(cfg, state, state.secrets(cfg)).playbook("destroy.yml", label="destroy")
            ssh = Ssh(state.ensure_ssh_key(cfg), state.known_hosts)
            for device in (h for h in hosts if h.role == "device"):
                ssh.run(device, "systemctl disable --now meteorcli; "
                                "wg-quick down /etc/meteorcli/wireguard/wg0.conf; docker rm -f e2e-app demo-app", sudo=True)
    if state.tfstate.is_file():
        say(f"[terraform] destroying resources tracked in {state.tfstate}")
        tf.init()
        tf.destroy()
        left = tf.resources()
        if left:
            say(f"error: Terraform state still lists {len(left)} resources")
            return EXIT_INFRA
    state.update(hosts=[], destroyed_at=time.time())
    say(f"down: environment {cfg.env_name} removed (state kept in {state.dir} for the reports)")
    return EXIT_OK


COMMANDS = {
    "validate": (cmd_validate, "Check settings, tools, credentials and DNS prerequisites"),
    "up": (cmd_up, "Provision, configure, and run the full acceptance suite"),
    "seed": (cmd_seed, "Create demo data: organization, demo account, devices, a deployed app"),
    "test": (cmd_test, "Run the acceptance suite against the existing environment"),
    "status": (cmd_status, "Show hosts, URLs and the last result"),
    "logs": (cmd_logs, "Collect redacted service logs from every host"),
    "down": (cmd_down, "Destroy only this environment's tracked resources"),
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="meteorcloud-testenv", description=__doc__)
    root.add_argument("--env-file", type=Path, help="settings file (default tests/e2e/.env)")
    sub = root.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in COMMANDS.items():
        command = sub.add_parser(name, help=help_text)
        if name in ("up", "test"):
            command.add_argument("--only", action="append", metavar="STAGE_OR_CHECK",
                                 help="run only these stages/check ids (repeatable)")
            command.add_argument("--destroy-on-failure", action="store_true",
                                 help="destroy the environment if anything fails (default: keep it)")
        if name == "up":
            command.add_argument("--no-tests", action="store_true",
                                 help="only create and configure the machines (then run `seed`)")
        if name == "down":
            command.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        cfg = config_mod.load(args.env_file)
    except ConfigError as exc:
        say(f"configuration error:\n{exc}")
        return EXIT_CONFIG
    state = State(cfg.env_name)
    func, _ = COMMANDS[args.command]
    try:
        return func(cfg, state, args)
    except KeyboardInterrupt:
        say("interrupted; the environment is kept")
        return EXIT_FAILED
    except CommandError as exc:
        say(f"error: {exc}\n{exc.tail[-2000:]}")
        return EXIT_INFRA


if __name__ == "__main__":
    sys.exit(main())
