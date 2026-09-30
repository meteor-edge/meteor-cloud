"""Owner-only secret files for meteorcli (API key and claim secret)."""

from __future__ import annotations

from pathlib import Path

from edge_agent.credentials import read_device_token, remove_file, write_device_token


def write_secret(path: Path, value: str) -> None:
    write_device_token(path, value)


def read_secret(path: Path) -> str | None:
    return read_device_token(path)


def remove_secret(path: Path) -> None:
    remove_file(path)
