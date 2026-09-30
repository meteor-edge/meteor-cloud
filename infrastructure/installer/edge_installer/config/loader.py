"""Configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from edge_installer.config.models import InstallationConfig
from edge_installer.exceptions import ConfigurationError


def load_configuration(path: Path) -> InstallationConfig:
    if not path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {path}",
            stage="configuration",
        )
    if not path.is_file():
        raise ConfigurationError(f"Configuration path is not a file: {path}", stage="configuration")

    try:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}", stage="configuration") from exc
    except OSError as exc:
        raise ConfigurationError(f"Unable to read {path}: {exc}", stage="configuration") from exc

    if raw is None:
        raise ConfigurationError(f"Configuration file is empty: {path}", stage="configuration")
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"Configuration root must be a mapping in {path}",
            stage="configuration",
        )

    try:
        return InstallationConfig.model_validate(raw)
    except ValidationError as exc:
        lines = [f"Invalid configuration in {path}:"]
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "(root)"
            lines.append(f"- {location}: {error['msg']}")
        raise ConfigurationError("\n".join(lines), stage="configuration") from exc


def load_raw_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigurationError(f"Expected mapping in {path}", stage="configuration")
    return data
