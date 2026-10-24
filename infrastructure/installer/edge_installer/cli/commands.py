"""CLI command implementations."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

from edge_installer.config.loader import load_configuration
from edge_installer.config.validation import (
    validate_aws_credentials,
    validate_configuration,
    validate_dependencies,
)
from edge_installer.deployment.dns import dns_records
from edge_installer.deployment.prepare import prepare_apply
from edge_installer.deployment.service import PlatformDeploymentService
from edge_installer.exceptions import ConfigurationError, InstallerError

SAMPLE_CONFIG = (
    Path(__file__).resolve().parents[1] / "config" / "examples" / "installation.yaml"
)


def _load(config_path: Path) -> PlatformDeploymentService:
    config = load_configuration(config_path)
    return PlatformDeploymentService(config)


def run_init(*, output: Path, console: Console) -> None:
    if output.exists():
        console.print(f"[yellow]Configuration already exists at {output}[/yellow]")
        raise SystemExit(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLE_CONFIG, output)
    console.print(f"[green]Created sample configuration at {output}[/green]")


def run_validate(*, config_path: Path, console: Console) -> None:
    try:
        config = load_configuration(config_path)
        errors = validate_configuration(config) + validate_dependencies(config)
        profile = config.aws.profile if config.aws else None
        errors.extend(validate_aws_credentials(profile))
        if errors:
            console.print("[red]Configuration is invalid:[/red]")
            for item in errors:
                console.print(f"  - {item}")
            raise SystemExit(1)
    except ConfigurationError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(1) from exc
    console.print("[green]Configuration is valid.[/green]")


def run_plan(*, config_path: Path, console: Console) -> None:
    service = _load(config_path)
    try:
        result = service.plan()
    except InstallerError as exc:
        console.print(f"[red]Plan failed[/red] ({exc.stage}): {exc.message}")
        raise SystemExit(1) from exc
    console.print("[bold]Terraform plan summary[/bold]")
    console.print(result.get("plan_output", ""))


def run_apply(*, config_path: Path, console: Console, demo: bool | None = None) -> None:
    try:
        config = load_configuration(config_path)
        prepare_apply(config, config_path=config_path, console=console, demo=demo)
    except ConfigurationError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(1) from exc
    service = PlatformDeploymentService(config)
    try:
        result = service.apply()
    except InstallerError as exc:
        console.print(f"[red]Apply failed[/red] at stage '{exc.stage}': {exc.message}")
        raise SystemExit(1) from exc

    console.print("\n[green bold]Installation completed successfully.[/green bold]\n")
    console.print(f"Installation: {config.installation.name}")
    console.print(f"Services: {', '.join(config.enabled_service_names())}")
    console.print(f"Provider: {config.installation.provider}")
    console.print(f"Region: {config.aws.region if config.aws else result.outputs.region}")
    console.print(f"Instance ID: {result.outputs.instance_id}")
    console.print(f"Public IP: {result.outputs.public_ip}")
    if result.outputs.vpn_gateway_public_ip:
        port = config.services.vpn.listen_port
        console.print(f"WireGuard gateway: {result.outputs.vpn_gateway_public_ip}:{port} (UDP)")
    console.print(f"Platform URL: {result.state.platform_url}")
    for key, value in result.health.items():
        console.print(f"{key.replace('_', ' ').title()}: {value}")
    records = dns_records(config, result.outputs)
    if records:
        created = bool(config.aws and config.aws.dns_zone_id.strip() and config.platform.domain)
        title = (
            "Route53 records created"
            if created
            else "DNS records you need (A records, DNS only, no proxy; see docs/dns.md)"
        )
        console.print(f"\n[bold]{title}[/bold]")
        for name, ip, purpose in records:
            console.print(f"  {name:<32} -> {ip:<16} {purpose}")


def run_status(*, config_path: Path, console: Console) -> None:
    service = _load(config_path)
    try:
        result = service.status()
    except InstallerError as exc:
        console.print(f"[red]Status failed[/red]: {exc.message}")
        raise SystemExit(1) from exc
    console.print(result)


def run_upgrade(*, config_path: Path, console: Console) -> None:
    service = _load(config_path)
    try:
        result = service.upgrade()
    except InstallerError as exc:
        console.print(f"[red]Upgrade failed[/red]: {exc.message}")
        raise SystemExit(1) from exc
    console.print(f"[green]Upgraded to {result.state.platform_version}[/green]")
    console.print(f"Platform URL: {result.state.platform_url}")


def run_destroy(*, config_path: Path, force: bool, console: Console) -> None:
    service = _load(config_path)
    if not force:
        confirmed = console.input(
            f"Destroy installation and {service.config.installation.provider.upper()} resources? [y/N] "
        )
        if confirmed.strip().lower() not in {"y", "yes"}:
            console.print("Aborted.")
            raise SystemExit(0)
    try:
        service.destroy()
    except InstallerError as exc:
        console.print(f"[red]Destroy failed[/red]: {exc.message}")
        raise SystemExit(1) from exc
    console.print("[green]Installation destroyed.[/green]")
