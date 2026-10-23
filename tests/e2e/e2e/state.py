"""Per-environment working directory: tests/e2e/.state/<env>/ (gitignored, mode 0700)."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from e2e.config import E2E_DIR, Config
from e2e.redact import redact

SECRET_KEYS = (
    "EDGE_PLATFORM_POSTGRES_PASSWORD",
    "EDGE_PLATFORM_JWT_SECRET",
    "EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN",
    "EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID",
    "EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY",
    "EDGE_PLATFORM_ADMIN_EMAIL",
    "EDGE_PLATFORM_ADMIN_PASSWORD",
    "EDGE_PLATFORM_ACME_EMAIL",
)


@dataclass(frozen=True)
class Host:
    """A machine the suite talks to over SSH."""

    name: str
    role: str  # control_plane | gateway | device
    address: str
    user: str
    private_ip: str = ""
    instance_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Host:
        return cls(**{k: data.get(k, "") for k in ("name", "role", "address", "user", "private_ip", "instance_id")})


class State:
    def __init__(self, env_name: str, root: Path | None = None) -> None:
        self.env_name = env_name
        self.dir = (root or E2E_DIR / ".state") / env_name
        self.logs_dir = self.dir / "logs"
        self.build_dir = self.dir / "build"
        self.reports_dir = self.dir / "reports"

    # -- layout -----------------------------------------------------------------
    def ensure(self) -> None:
        for directory in (self.dir.parent, self.dir, self.logs_dir, self.build_dir, self.reports_dir):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)

    @property
    def exists(self) -> bool:
        return (self.dir / "state.json").is_file()

    @property
    def tfstate(self) -> Path:
        return self.dir / "terraform.tfstate"

    @property
    def tf_data_dir(self) -> Path:
        return self.dir / ".terraform"

    @property
    def tfvars(self) -> Path:
        return self.dir / "terraform.tfvars.json"

    @property
    def inventory(self) -> Path:
        return self.dir / "inventory.yml"

    @property
    def known_hosts(self) -> Path:
        return self.dir / "known_hosts"

    @property
    def ssh_key(self) -> Path:
        return self.dir / "id_ed25519"

    @property
    def secrets_file(self) -> Path:
        return self.dir / "secrets.env"

    # -- JSON state ---------------------------------------------------------------
    def load(self) -> dict[str, Any]:
        path = self.dir / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def save(self, data: dict[str, Any]) -> None:
        self.ensure()
        path = self.dir / "state.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def update(self, **changes: Any) -> dict[str, Any]:
        data = self.load()
        data.update(changes)
        self.save(data)
        return data

    def hosts(self) -> list[Host]:
        return [Host.from_dict(h) for h in self.load().get("hosts", [])]

    def host(self, role: str, index: int = 0) -> Host | None:
        matching = [h for h in self.hosts() if h.role == role]
        return matching[index] if index < len(matching) else None

    # -- secrets ------------------------------------------------------------------
    def ensure_ssh_key(self, cfg: Config) -> Path:
        """Per-environment key (AWS). On-prem uses E2E_SSH_KEY_FILE."""
        if cfg.provider == "onprem":
            return Path(cfg.ssh_key_file).expanduser()
        self.ensure()
        if not self.ssh_key.is_file():
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"meteorcloud-e2e-{self.env_name}",
                 "-f", str(self.ssh_key)],
                check=True,
            )
        os.chmod(self.ssh_key, 0o600)
        return self.ssh_key

    def secrets(self, cfg: Config) -> dict[str, str]:
        """Generated once per environment; never written to the inventory or logs."""
        self.ensure()
        values: dict[str, str] = {}
        if self.secrets_file.is_file():
            for line in self.secrets_file.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep:
                    values[key] = value
        defaults = {
            "EDGE_PLATFORM_POSTGRES_PASSWORD": secrets.token_urlsafe(24),
            "EDGE_PLATFORM_JWT_SECRET": secrets.token_urlsafe(48),
            "EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN": secrets.token_urlsafe(32),
            "EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID": "e2e" + secrets.token_hex(8),
            "EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY": secrets.token_urlsafe(32),
            "EDGE_PLATFORM_ADMIN_EMAIL": f"e2e-admin@{cfg.fqdn or 'example.com'}",
            "EDGE_PLATFORM_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "EDGE_PLATFORM_ACME_EMAIL": cfg.acme_email,
        }
        changed = False
        for key, value in defaults.items():
            if key not in values:
                values[key] = value
                changed = True
        if changed:
            fd = os.open(self.secrets_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.writelines(f"{k}={v}\n" for k, v in values.items())
        os.chmod(self.secrets_file, 0o600)
        for key in SECRET_KEYS:
            if key not in ("EDGE_PLATFORM_ADMIN_EMAIL", "EDGE_PLATFORM_ACME_EMAIL"):
                redact.add(values.get(key))
        return values
