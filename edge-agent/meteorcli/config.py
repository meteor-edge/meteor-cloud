"""Filesystem locations and API-base derivation for meteorcli."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path

from edge_agent.config import AgentConfig, AgentPaths, load_config, save_config

SYSTEM_CONFIG_DIR = Path("/etc/meteorcli")


def _sudo_user() -> str | None:
    name = os.environ.get("SUDO_USER")
    if not name or name == "root":
        return None
    return name


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def _user_home(username: str | None = None) -> Path:
    if username:
        try:
            import pwd

            return Path(pwd.getpwnam(username).pw_dir)
        except (ImportError, KeyError, OSError):
            pass
    return Path.home()


def default_config_dir() -> Path:
    """Per-user config dir, including when the command is run via sudo.

    Only an interactive root login (no SUDO_USER) uses ``/etc/meteorcli``.
    """
    sudo_user = _sudo_user()
    if _is_root() and sudo_user is None:
        return SYSTEM_CONFIG_DIR
    if sudo_user:
        return _user_home(sudo_user) / ".config" / "meteorcli"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "meteorcli"
    return Path.home() / ".config" / "meteorcli"


def chown_config_dir(path: Path) -> None:
    """If running under sudo, give the invoking user ownership of their config."""
    sudo_user = _sudo_user()
    if not sudo_user or not _is_root() or not path.exists():
        return
    try:
        import pwd

        pw = pwd.getpwnam(sudo_user)
    except (ImportError, KeyError, OSError):
        return
    os.chown(path, pw.pw_uid, pw.pw_gid)
    for dirpath, dirnames, filenames in os.walk(path):
        os.chown(dirpath, pw.pw_uid, pw.pw_gid)
        for name in dirnames + filenames:
            os.chown(os.path.join(dirpath, name), pw.pw_uid, pw.pw_gid)


@dataclass
class CliPaths:
    config_path: Path
    token_path: Path
    api_key_path: Path
    claim_secret_path: Path

    def agent_paths(self) -> AgentPaths:
        return AgentPaths(config_path=self.config_path, token_path=self.token_path)

    @classmethod
    def from_dir(cls, base: Path) -> CliPaths:
        return cls(
            config_path=base / "config.json",
            token_path=base / "device-token",
            api_key_path=base / "api-key",
            claim_secret_path=base / "claim-secret",
        )


def _split_scheme_host(value: str) -> tuple[str | None, str]:
    raw = value.strip()
    scheme = None
    lower = raw.lower()
    if lower.startswith("https://"):
        scheme = "https"
        raw = raw[8:]
    elif lower.startswith("http://"):
        scheme = "http"
        raw = raw[7:]
    host = raw.split("/")[0]
    if host.lower().startswith("www."):
        host = host[4:]
    return scheme, host


def _hostname_of(host: str) -> str:
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def _host_is_local(host: str) -> bool:
    hostname = _hostname_of(host).lower()
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def derive_api_base(domain: str, *, http: bool = False) -> str:
    """Turn a domain, IP, or URL into an API origin.

    The configured host *is* the API. Nothing is rewritten to ``api.<host>``.
    IP addresses and localhost use HTTP. A leftover ``api.<ip>`` value from
    older configs is stripped so the CLI talks to the IP itself.
    """
    scheme, host = _split_scheme_host(domain)
    if not host:
        raise ValueError("domain is empty")
    if host.lower().startswith("api.") and _host_is_local(host[4:]):
        host = host[4:]

    local = _host_is_local(host)
    if http or scheme == "http" or (scheme is None and local):
        use_http = True
    elif local:
        # Older configs stored https://api.<ip>; still use HTTP to the IP.
        use_http = True
    else:
        use_http = False
    chosen = "http" if use_http else "https"
    return f"{chosen}://{host}"


def resolve_server_url(config: AgentConfig | None, *, override: str | None = None) -> str | None:
    if override:
        return derive_api_base(override).rstrip("/")
    if config is None:
        return None
    if config.api_base:
        return derive_api_base(config.api_base).rstrip("/")
    if config.domain:
        return derive_api_base(config.domain)
    if config.server_url:
        return derive_api_base(config.server_url).rstrip("/")
    return None


def persist_connection(
    paths: CliPaths,
    *,
    domain: str | None = None,
    api_base: str | None = None,
    http: bool = False,
) -> AgentConfig:
    existing = load_config(paths.agent_paths()) or AgentConfig(server_url="")
    if domain is not None:
        existing.domain = domain
        if api_base is None and not http:
            existing.api_base = None
    if api_base is not None:
        existing.api_base = derive_api_base(api_base, http=http)
    elif http and (domain or existing.domain):
        existing.api_base = derive_api_base(domain or existing.domain or "", http=True)
    resolved = resolve_server_url(existing)
    if resolved:
        existing.server_url = resolved
    save_config(paths.agent_paths(), existing)
    return existing
