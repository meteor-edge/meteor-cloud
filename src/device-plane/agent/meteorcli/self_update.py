"""Find the meteorcli updater and forward arguments to it.

The procedure is ``packaging/update-meteorcli.sh`` (installed as
``meteorcli-update``). A device that does not have the ``update`` command yet
can run that script from a GitHub Release. This module does not download or
replace an install itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_RELEASE_UPDATER = "https://github.com/meteor-edge/meteor-cloud/releases/latest/download/update-meteorcli.sh"


def update_script_path() -> Path | None:
    override = os.environ.get("METEORCLI_UPDATE_SCRIPT")
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    packaged = Path(__file__).resolve().parents[1] / "packaging" / "update-meteorcli.sh"
    if packaged.is_file():
        return packaged
    for candidate in (
        Path("/usr/local/libexec/meteorcli-update"),
        Path.home() / ".local/libexec/meteorcli-update",
    ):
        if candidate.is_file():
            return candidate
    return None


def build_update_args(
    *,
    check: bool = False,
    yes: bool = False,
    to: str | None = None,
    allow_downgrade: bool = False,
    rollback: bool = False,
) -> list[str]:
    args: list[str] = []
    if rollback:
        args.append("--rollback")
    if check:
        args.append("--check")
    if yes:
        args.append("--yes")
    if allow_downgrade:
        args.append("--allow-downgrade")
    if to:
        args.extend(["--to", to])
    return args


def run_self_update(argv: list[str], *, config_dir: str | None = None) -> int:
    script = update_script_path()
    if script is None:
        print("error: meteorcli updater is not installed.", file=sys.stderr)
        print(
            f"Run: curl -fsSL {_RELEASE_UPDATER} | sudo bash -s -- --yes",
            file=sys.stderr,
        )
        return 1
    env = os.environ.copy()
    if config_dir:
        env["METEORCLI_CONFIG_DIR"] = config_dir
    completed = subprocess.run([str(script), *argv], check=False, env=env)
    return int(completed.returncode)
