"""Agent filesystem paths and non-secret configuration persistence.

Only non-secret configuration (server URL, device identifiers, heartbeat
interval) is stored in the config file. The device credential is stored
separately with strict permissions by :mod:`edge_agent.credentials`. All paths
are injectable so tests never touch the real system locations.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CONFIG_DIR = Path("/etc/edge-agent")
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_TOKEN_PATH = DEFAULT_CONFIG_DIR / "device-token"


@dataclass
class AgentPaths:
    """Filesystem locations used by the agent."""

    config_path: Path = DEFAULT_CONFIG_PATH
    token_path: Path = DEFAULT_TOKEN_PATH

    @classmethod
    def default(cls) -> AgentPaths:
        return cls()


@dataclass
class AgentConfig:
    """Non-secret agent configuration persisted between runs."""

    server_url: str
    device_id: str = ""
    organization_id: str = ""
    name: str = ""
    heartbeat_interval_seconds: int = 60
    domain: str | None = None
    api_base: str | None = None


def load_config(paths: AgentPaths) -> AgentConfig | None:
    """Load persisted configuration, or None when it does not exist."""
    try:
        raw = paths.config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(raw)
    return AgentConfig(
        server_url=data.get("server_url") or "",
        device_id=data.get("device_id") or "",
        organization_id=data.get("organization_id") or "",
        name=data.get("name") or "",
        heartbeat_interval_seconds=int(data.get("heartbeat_interval_seconds", 60)),
        domain=data.get("domain"),
        api_base=data.get("api_base"),
    )


def _atomic_write(path: Path, data: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        # Best-effort cleanup so a failed write never leaves a temp file behind.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_config(paths: AgentPaths, config: AgentConfig) -> None:
    """Persist non-secret configuration as JSON (mode 0644)."""
    _atomic_write(paths.config_path, json.dumps(asdict(config), indent=2), mode=0o644)
