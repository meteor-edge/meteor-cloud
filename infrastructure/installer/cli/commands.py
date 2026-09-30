"""CLI command implementations for the Edge Platform installer."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from rich.console import Console

from config.loader import ConfigurationError, load_configuration
from deployment.service import PlatformDeployment
from providers.registry import get_provider

logger = logging.getLogger(__name__)

SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "examples" / "installation.yaml"


def run_init(*, output: Path, console: Console) -> None:
    """Copy the sample configuration to the given path."""
    if output.exists():
        console.print(f"[yellow]Configuration already exists at {output}[/yellow]")
        raise SystemExit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLE_CONFIG, output)
    console.print(f"[green]Created sample configuration at {output}[/green]")
    console.print("Edit the file, then run: [bold]edge-installer validate[/bold]")


def run_validate(*, config_path: Path, console: Console) -> None:
    """Load and validate the configuration file."""
    try:
        config = load_configuration(config_path)
    except ConfigurationError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise SystemExit(1) from exc

    console.print("[green]Configuration is valid.[/green]")
    console.print(f"  Installation: {config.installation.name}")
    console.print(f"  Provider:     {config.installation.provider}")
    console.print(f"  Environment:  {config.installation.environment}")
    console.print(f"  Platform:     {config.platform.version}")


def run_plan(*, config_path: Path, console: Console) -> None:
    """Plan infrastructure and platform changes."""
    config = _require_config(config_path, console)
    deployment = PlatformDeployment(config)
    console.print("[bold]Planning installation changes...[/bold]")
    try:
        result = deployment.plan()
    except NotImplementedError:
        console.print("[yellow]Not implemented yet[/yellow]")
        console.print("Planning will provision infrastructure and prepare platform components.")
        return
    console.print(result)


def run_apply(*, config_path: Path, console: Console) -> None:
    """Apply the installation."""
    config = _require_config(config_path, console)
    deployment = PlatformDeployment(config)
    console.print("[bold]Applying installation...[/bold]")
    try:
        result = deployment.apply()
    except NotImplementedError:
        console.print("[yellow]Not implemented yet[/yellow]")
        console.print("Apply will create infrastructure and install platform components.")
        return
    console.print(result)


def run_status(*, config_path: Path, console: Console) -> None:
    """Report installation status."""
    config = _require_config(config_path, console)
    provider = get_provider(config.installation.provider)
    console.print("[bold]Installation status[/bold]")
    console.print(f"  Name:        {config.installation.name}")
    console.print(f"  Provider:    {config.installation.provider}")
    console.print(f"  Environment: {config.installation.environment}")
    try:
        details = provider.inspect()
        console.print(details)
    except NotImplementedError:
        console.print("[yellow]Not implemented yet[/yellow]")
        console.print("Status inspection will report infrastructure and component health.")


def run_upgrade(*, config_path: Path, console: Console) -> None:
    """Upgrade the platform installation."""
    config = _require_config(config_path, console)
    deployment = PlatformDeployment(config)
    console.print("[bold]Upgrading installation...[/bold]")
    try:
        result = deployment.upgrade()
    except NotImplementedError:
        console.print("[yellow]Not implemented yet[/yellow]")
        console.print("Upgrade will update platform components to the configured version.")
        return
    console.print(result)


def run_destroy(*, config_path: Path, force: bool, console: Console) -> None:
    """Destroy the installation."""
    config = _require_config(config_path, console)
    if not force:
        confirmed = console.input(
            f"Destroy installation [bold]{config.installation.name}[/bold]? [y/N] "
        )
        if confirmed.strip().lower() not in {"y", "yes"}:
            console.print("Aborted.")
            raise SystemExit(0)

    deployment = PlatformDeployment(config)
    console.print("[bold]Destroying installation...[/bold]")
    try:
        result = deployment.destroy()
    except NotImplementedError:
        console.print("[yellow]Not implemented yet[/yellow]")
        console.print("Destroy will remove platform components and tear down infrastructure.")
        return
    console.print(result)


def _require_config(config_path: Path, console: Console):
    try:
        return load_configuration(config_path)
    except ConfigurationError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise SystemExit(1) from exc
