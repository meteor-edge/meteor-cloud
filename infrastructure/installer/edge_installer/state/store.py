"""Atomic installer state persistence."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from edge_installer.exceptions import InstallationLockedError, StateError
from edge_installer.state.models import InstallationState
from edge_installer.state.paths import installation_state_file, lock_file


def load_state(name: str) -> InstallationState | None:
    path = installation_state_file(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return InstallationState.model_validate(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise StateError(f"Unable to read installer state at {path}") from exc


def save_state(state: InstallationState) -> None:
    path = installation_state_file(state.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now(UTC)
    temp = path.with_suffix(".json.tmp")
    try:
        temp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        raise StateError(f"Unable to write installer state at {path}") from exc


class InstallationLock:
    """Local file lock for installer operations."""

    def __init__(self, name: str) -> None:
        self.path = lock_file(name)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                contents = self.path.read_text(encoding="utf-8").strip()
                pid = int(contents) if contents else -1
            except ValueError:
                pid = -1
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    raise InstallationLockedError(
                        f"Installation '{self.path.parent.name}' is locked by process {pid}.",
                        stage="locking",
                    )
                except OSError:
                    self.path.unlink(missing_ok=True)

        self.path.write_text(str(os.getpid()), encoding="utf-8")

    def release(self) -> None:
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> InstallationLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
