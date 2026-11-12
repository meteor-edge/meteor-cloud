"""Read a first-boot settings file (meteorenv) from the boot partition.

Same file names and locations as ``meteorcli-firstboot.sh`` / ``meteorcli-settings.sh``.
Used by ``meteorcli.service`` so a card with ``meteorenv.txt`` can enroll without the
separate first-boot units (stock OS + installed meteorcli).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("meteorcli")

_FILE_NAMES = (".meteorenv", "meteorenv", "meteorenv.txt", ".meteorenv.txt")
_BOOT_DIRS = (
    Path("/boot/firmware"),
    Path("/boot"),
)
# Only enrollment fields are applied by the agent; Wi-Fi / user / SSH need meteorcli-settings.
_ENROLL_KEYS = frozenset({"METEOR_DOMAIN", "METEOR_API_BASE", "METEOR_API_KEY", "METEOR_NAME"})
_LINE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")


def boot_dirs() -> list[Path]:
    dirs = list(_BOOT_DIRS)
    override = os.environ.get("METEOR_BOOT_DIR", "").strip()
    if override:
        dirs.insert(0, Path(override))
    return dirs


def find_meteorenv_files(*, directories: list[Path] | None = None) -> list[Path]:
    """Existing meteorenv files on the boot partition, in preference order."""
    found: list[Path] = []
    for directory in directories if directories is not None else boot_dirs():
        if not directory.is_dir():
            continue
        for name in _FILE_NAMES:
            path = directory / name
            if path.is_file() and path not in found:
                found.append(path)
    return found


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_meteorenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Does not log values (they may hold secrets)."""
    values: dict[str, str] = {}
    if text.startswith("\ufeff"):
        text = text[1:]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None:
            continue
        values[match.group("key")] = _unquote(match.group("value"))
    return values


def read_enrollment(path: Path) -> dict[str, str]:
    """Enrollment fields from one meteorenv file (domain, key, optional name)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s (%s).", path, type(exc).__name__)
        return {}
    parsed = parse_meteorenv(text)
    return {key: parsed[key] for key in _ENROLL_KEYS if parsed.get(key)}


def consume_meteorenv(path: Path) -> None:
    """Remove the file after enrollment settings were taken from it (same as first-boot)."""
    try:
        path.unlink()
        logger.info("Removed %s after reading enrollment settings.", path)
    except OSError as exc:
        logger.warning("Could not remove %s (%s).", path, type(exc).__name__)
