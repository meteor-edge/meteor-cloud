"""Edge Platform installer CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler

from edge_installer.cli import commands

app = typer.Typer(
    name="edge-installer",
    help="Install and maintain the Edge Platform control plane on AWS.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

ConfigArgument = Annotated[
    Path | None,
    typer.Argument(help="Path to installation configuration YAML."),
]
ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to installation configuration YAML."),
]


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _resolve_config(config_arg: Path | None, config_opt: Path | None) -> Path:
    return config_opt or config_arg or Path("installation.yaml")


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging.")] = False,
) -> None:
    _configure_logging(verbose)


@app.command()
def init(
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("installation.yaml"),
) -> None:
    commands.run_init(output=output, console=console)


@app.command()
def validate(
    config_arg: ConfigArgument = None,
    config: ConfigOption = None,
) -> None:
    commands.run_validate(config_path=_resolve_config(config_arg, config), console=console)


@app.command()
def plan(
    config_arg: ConfigArgument = None,
    config: ConfigOption = None,
) -> None:
    commands.run_plan(config_path=_resolve_config(config_arg, config), console=console)


@app.command()
def apply(
    config_arg: ConfigArgument = None,
    config: ConfigOption = None,
) -> None:
    commands.run_apply(config_path=_resolve_config(config_arg, config), console=console)


@app.command()
def status(
    config_arg: ConfigArgument = None,
    config: ConfigOption = None,
) -> None:
    commands.run_status(config_path=_resolve_config(config_arg, config), console=console)


@app.command()
def upgrade(
    config_arg: ConfigArgument = None,
    config: ConfigOption = None,
) -> None:
    commands.run_upgrade(config_path=_resolve_config(config_arg, config), console=console)


@app.command()
def destroy(
    config_arg: ConfigArgument = None,
    config: ConfigOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation.")] = False,
    force: Annotated[bool, typer.Option("--force", hidden=True)] = False,
) -> None:
    commands.run_destroy(
        config_path=_resolve_config(config_arg, config),
        force=yes or force,
        console=console,
    )


if __name__ == "__main__":
    app()
