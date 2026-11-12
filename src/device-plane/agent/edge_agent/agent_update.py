"""Start the safe meteorcli updater and decide when an update_agent action is done.

The updater runs as its own process so a service restart does not kill it. Outcome
is judged from the version that is running after the updater finishes, compared
with the version stored when the console ordered the update.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("edge_agent")

PENDING_NAME = "pending-agent-update.json"
# Match the console plan timeout: give the updater this long after it is started.
UPDATE_TIMEOUT_SECONDS = 30 * 60
RELEASE_UPDATER = (
    "https://github.com/meteor-edge/meteor-cloud/releases/latest/download/update-meteorcli.sh"
)


def _parse_version(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("meteorcli "):
        cleaned = cleaned[len("meteorcli ") :].strip()
    parts = cleaned.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return cleaned


def version_is_newer(candidate: str | None, baseline: str | None) -> bool:
    """True when candidate is a higher X.Y.Z than baseline."""
    left = _parse_version(candidate)
    right = _parse_version(baseline)
    if left is None:
        return False
    if right is None:
        return True
    return tuple(int(part) for part in left.split(".")) > tuple(int(part) for part in right.split("."))


def current_agent_version() -> str | None:
    try:
        from meteorcli import __version__
    except ImportError:
        return None
    return _parse_version(str(__version__))


def find_updater_script() -> Path | None:
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


def pending_path(config_dir: Path) -> Path:
    return config_dir / PENDING_NAME


def load_pending(config_dir: Path) -> dict[str, Any] | None:
    path = pending_path(config_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_pending(config_dir: Path) -> None:
    try:
        pending_path(config_dir).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove %s", pending_path(config_dir))


def write_pending(
    config_dir: Path,
    *,
    action_id: object,
    from_version: str | None,
) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "action_id": str(action_id),
        "from_version": from_version,
        "started_at": time.time(),
    }
    path = pending_path(config_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def updater_lock_held() -> bool:
    """True when another meteorcli update still holds the update lock."""
    lock = Path(os.environ.get("METEORCLI_UPDATE_LOCK", f"{os.environ.get('TMPDIR', '/tmp')}/meteorcli-update.lock"))
    if not lock.is_file():
        return False
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return False
    try:
        with lock.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
    except OSError:
        return False


def start_updater(*, config_dir: Path, action_id: object, from_version: str | None) -> str | None:
    """Start update-meteorcli.sh --yes detached. Returns an error string, or None on success."""
    script = find_updater_script()
    if script is None:
        return (
            "meteorcli updater is not installed. "
            f"On the device run: curl -fsSL {RELEASE_UPDATER} | sudo bash -s -- --yes"
        )
    write_pending(config_dir, action_id=action_id, from_version=from_version)
    log_path = config_dir / "agent-update.log"
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n--- update_agent {action_id} starting ---\n")
            log.flush()
            subprocess.Popen(  # noqa: S603 — fixed argv; script path from install
                [str(script), "--yes"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "METEORCLI_CONFIG_DIR": str(config_dir)},
            )
    except OSError as error:
        clear_pending(config_dir)
        return f"Could not start the meteorcli updater: {error}"
    logger.info("Started meteorcli updater for action %s", action_id)
    return None


def settle_pending(config_dir: Path) -> dict[str, Any] | None:
    """If a started update has finished, return a report result dict; otherwise None."""
    pending = load_pending(config_dir)
    if pending is None:
        return None
    action_id = pending.get("action_id")
    if not action_id:
        clear_pending(config_dir)
        return None
    from_version = pending.get("from_version")
    started_at = float(pending.get("started_at") or 0)
    if updater_lock_held():
        if started_at and time.time() - started_at > UPDATE_TIMEOUT_SECONDS:
            clear_pending(config_dir)
            return {
                "id": action_id,
                "state": "failed",
                "error": "The meteorcli update did not finish in time.",
            }
        return None
    current = current_agent_version()
    if version_is_newer(current, from_version if isinstance(from_version, str) else None):
        clear_pending(config_dir)
        return {"id": action_id, "state": "done"}
    if started_at and time.time() - started_at > UPDATE_TIMEOUT_SECONDS:
        clear_pending(config_dir)
        return {
            "id": action_id,
            "state": "failed",
            "error": f"meteorcli stayed at {current or 'unknown'} after the update.",
        }
    # Updater exited; version did not advance (including automatic rollback).
    clear_pending(config_dir)
    return {
        "id": action_id,
        "state": "failed",
        "error": f"meteorcli stayed at {current or 'unknown'}; the previous install was kept.",
    }
