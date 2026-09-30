"""Installer state directory paths."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def infrastructure_root() -> Path:
    return repo_root() / "infrastructure"


def state_root() -> Path:
    return repo_root() / ".installer-state"


def installation_dir(name: str) -> Path:
    return state_root() / name


def installation_state_file(name: str) -> Path:
    return installation_dir(name) / "installation.json"


def terraform_workdir(name: str) -> Path:
    return installation_dir(name) / "terraform"


def lock_file(name: str) -> Path:
    return installation_dir(name) / "install.lock"
