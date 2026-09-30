"""YAML configuration loader with helpful validation errors."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from config.models import InstallationConfig

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration cannot be loaded or validated."""


def load_configuration(path: Path) -> InstallationConfig:
    """Load and validate an installation configuration from a YAML file."""
    if not path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {path}\n"
            "Run 'edge-installer init' to create a sample configuration."
        )

    if not path.is_file():
        raise ConfigurationError(f"Configuration path is not a file: {path}")

    raw = _read_yaml(path)
    if raw is None:
        raise ConfigurationError(f"Configuration file is empty: {path}")

    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"Configuration root must be a mapping, got {type(raw).__name__} in {path}"
        )

    try:
        config = InstallationConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(path, exc)) from exc

    logger.debug("Loaded configuration from %s", path)
    return config


def _read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"Unable to read {path}: {exc}") from exc


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"Invalid configuration in {path}:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
