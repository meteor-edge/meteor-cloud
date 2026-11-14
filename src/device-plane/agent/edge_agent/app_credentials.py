"""Application credentials for workloads bound to a MeteorCloud application.

A release spec with ``meteorcloud: {application: ...}`` asks the agent to give the
workload an application identity. The agent obtains a device-scoped token from the
control plane, keeps it in a file that only root can reach on the host, mounts that
file read-only into the container at ``/run/secrets/meteorcloud-token`` and sets the
``METEORCLOUD_*`` environment variables the SDK reads.

The token never goes into an image, a compose file, the container environment or a
log line. It is replaced in place before it expires, so the running container sees
the new token without a restart.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edge_agent.client import AgentApiError, EdgeClient

logger = logging.getLogger("edge_agent.app_credentials")

TOKEN_MOUNT_PATH = "/run/secrets/meteorcloud-token"
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


class AppCredentialError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AppCredential:
    token_path: Path
    env: dict[str, str]

    @property
    def volume(self) -> str:
        return f"{self.token_path}:{TOKEN_MOUNT_PATH}:ro"


def binding_of(item: dict[str, Any]) -> dict[str, Any] | None:
    """The ``meteorcloud`` binding of a desired workload, if it has one."""
    binding = (item.get("spec") or {}).get("meteorcloud")
    if isinstance(binding, dict) and binding.get("application"):
        return binding
    return None


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class AppCredentialStore:
    """Token files and their metadata, one directory per workload."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _dir(self, workload_key: str) -> Path:
        # A readable prefix plus a digest of the full key: two keys that differ only in
        # characters unsafe for a file name never share a directory.
        prefix = _UNSAFE.sub("_", workload_key).strip(".")[:48] or "_"
        return self.root / f"{prefix}-{hashlib.sha256(workload_key.encode()).hexdigest()[:12]}"

    def token_path(self, workload_key: str) -> Path:
        return self._dir(workload_key) / "token"

    def _meta(self, workload_key: str) -> dict[str, Any] | None:
        try:
            meta = json.loads((self._dir(workload_key) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return meta if isinstance(meta, dict) else None

    def current(self, workload_key: str, binding: dict[str, Any]) -> AppCredential | None:
        """The stored credential, if it exists and still belongs to this binding."""
        meta = self._meta(workload_key)
        path = self.token_path(workload_key)
        if meta is None or not path.is_file() or path.stat().st_size == 0:
            return None
        if meta.get("application") != binding.get("application"):
            return None
        if meta.get("environment") != (binding.get("environment") or "production"):
            return None
        return AppCredential(token_path=path, env=dict(meta.get("env") or {}))

    def refresh_due(self, workload_key: str, now: datetime | None = None) -> bool:
        meta = self._meta(workload_key)
        refresh_after = _parse_time((meta or {}).get("refresh_after"))
        return refresh_after is None or (now or datetime.now(UTC)) >= refresh_after

    def expired(self, workload_key: str, now: datetime | None = None) -> bool:
        meta = self._meta(workload_key)
        expires_at = _parse_time((meta or {}).get("expires_at"))
        return expires_at is None or (now or datetime.now(UTC)) >= expires_at

    def mint(self, client: EdgeClient, device_token: str, workload_key: str) -> AppCredential:
        """Obtain a new token and store it. The previous token stays valid for a short grace period."""
        try:
            issued = client.mint_app_credential(device_token=device_token, workload_key=workload_key)
        except AgentApiError as error:
            raise AppCredentialError(f"Could not obtain application credentials: {error.message}") from error
        token = issued.get("token")
        if not token:
            raise AppCredentialError("The control plane answered without an application token.")
        env = {
            "METEORCLOUD_TOKEN_FILE": TOKEN_MOUNT_PATH,
            "METEORCLOUD_APP_ID": str(issued.get("application") or ""),
            "METEORCLOUD_DEVICE_ID": str(issued.get("device_id") or ""),
            "METEORCLOUD_ENVIRONMENT": str(issued.get("environment") or ""),
            "METEORCLOUD_ORGANIZATION_ID": str(issued.get("organization_id") or ""),
        }
        if issued.get("endpoint"):
            env["METEORCLOUD_ENDPOINT"] = str(issued["endpoint"])

        directory = self._dir(workload_key)
        directory.mkdir(parents=True, exist_ok=True)
        # Only root may enter the directory on the host. The file itself is world-readable
        # so a container that does not run as root can still read its own mounted token.
        os.chmod(self.root, 0o700)
        os.chmod(directory, 0o700)
        path = directory / "token"
        # Written in place (same inode): a single-file bind mount keeps pointing at it.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(token) + "\n")
        os.chmod(path, 0o644)
        meta = {
            "application": issued.get("application"),
            "environment": issued.get("environment"),
            "credential_id": issued.get("credential_id"),
            "expires_at": issued.get("expires_at"),
            "refresh_after": issued.get("refresh_after"),
            "env": env,
        }
        meta_path = directory / "meta.json"
        tmp = meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(meta_path)
        logger.info("Stored application credentials for %s (application %s)", workload_key, meta["application"])
        return AppCredential(token_path=path, env=env)

    def for_apply(self, client: EdgeClient, device_token: str, item: dict[str, Any]) -> AppCredential | None:
        """Credential to start a workload with: reuse a valid one, otherwise mint."""
        binding = binding_of(item)
        if binding is None:
            return None
        workload_key = str(item["workload_key"])
        current = self.current(workload_key, binding)
        if current is not None and not self.refresh_due(workload_key):
            return current
        try:
            return self.mint(client, device_token, workload_key)
        except AppCredentialError:
            if current is not None and not self.expired(workload_key):
                return current  # offline: the stored token still works
            raise

    def keep_fresh(self, client: EdgeClient, device_token: str, item: dict[str, Any]) -> bool:
        """For a running workload: rotate the token when due. False if the workload must be re-applied."""
        binding = binding_of(item)
        if binding is None:
            return True
        workload_key = str(item["workload_key"])
        if self.current(workload_key, binding) is None:
            return False  # token file lost or binding changed: start the workload again with a new one
        if self.refresh_due(workload_key):
            try:
                self.mint(client, device_token, workload_key)
            except AppCredentialError as error:
                logger.warning("Application token for %s was not rotated: %s", workload_key, error.message)
            except OSError as error:
                # A full or read-only disk must not stop the other workloads.
                logger.warning("Application token for %s could not be written: %s", workload_key, error)
        return True

    def prune(self, keep: set[str]) -> None:
        """Delete stored tokens of workloads that are no longer desired or no longer bound."""
        if not self.root.is_dir():
            return
        wanted = {self._dir(key).name for key in keep}
        for entry in self.root.iterdir():
            if entry.is_dir() and entry.name not in wanted:
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("Removed application credentials of %s", entry.name)
