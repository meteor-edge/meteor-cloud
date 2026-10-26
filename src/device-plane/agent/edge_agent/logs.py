"""Device log sources (journal, systemd units, Docker containers, /var/log files).

The root-owned ``meteorcli-logs`` helper (installed by installcli.sh) does the
listing and printing. ``meteorcli logs`` wraps it on the device; the console
runs the same helper over SSH.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger("edge_agent")

# Root-owned helper installed by installcli.sh; runs via `sudo -n` for a non-root agent.
LOGS_HELPER = "/usr/local/libexec/meteorcli-logs"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def helper_command(*args: str) -> list[str]:
    """``meteorcli-logs`` argv, through sudo unless already root."""
    base = [LOGS_HELPER] if os.geteuid() == 0 else ["sudo", "-n", LOGS_HELPER]
    return [*base, *args]


def helper_installed() -> bool:
    return os.path.isfile(LOGS_HELPER)


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=30, check=False)


def list_log_sources(*, runner: Runner = _default_runner) -> list[dict[str, Any]] | None:
    """Sources the helper lists, or None when it is missing or fails."""
    if not helper_installed():
        return None
    try:
        result = runner(helper_command("list"))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("meteorcli-logs list failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.debug("meteorcli-logs list failed: %s", (result.stderr or "").strip()[:200])
        return None
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else None
