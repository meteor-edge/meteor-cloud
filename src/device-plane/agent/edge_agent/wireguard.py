"""Device-side WireGuard: generate keys on the device, never upload the private key."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("edge_agent")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=30)


def wireguard_available() -> bool:
    return shutil.which("wg") is not None and shutil.which("wg-quick") is not None


def sshd_available() -> bool:
    return Path("/usr/sbin/sshd").is_file() or shutil.which("sshd") is not None


def wireguard_dir(config_dir: Path) -> Path:
    return config_dir / "wireguard"


def ensure_keypair(directory: Path, *, runner: Runner = _default_runner) -> tuple[str, str]:
    """Return (private_key, public_key). Private key is written mode 0600."""
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)
    private_path = directory / "private.key"
    public_path = directory / "public.key"
    if private_path.is_file() and public_path.is_file():
        private_key = private_path.read_text(encoding="utf-8").strip()
        public_key = public_path.read_text(encoding="utf-8").strip()
        if private_key and public_key:
            return private_key, public_key
    private_key = runner(["wg", "genkey"]).stdout.strip()
    public_key = subprocess.run(
        ["wg", "pubkey"],
        input=private_key + "\n",
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    _write_secret(private_path, private_key)
    public_path.write_text(public_key + "\n", encoding="utf-8")
    os.chmod(public_path, stat.S_IRUSR | stat.S_IWUSR)
    return private_key, public_key


def _write_secret(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def render_client_config(
    *,
    private_key: str,
    address: str,
    server_public_key: str,
    server_endpoint: str,
    server_allowed_ips: str = "10.8.0.1/32",
    keepalive: int = 25,
) -> str:
    address_line = address if "/" in address else f"{address}/32"
    return (
        "[Interface]\n"
        f"Address = {address_line}\n"
        f"PrivateKey = {private_key}\n"
        "\n[Peer]\n"
        f"PublicKey = {server_public_key}\n"
        f"Endpoint = {server_endpoint}\n"
        f"AllowedIPs = {server_allowed_ips}\n"
        f"PersistentKeepalive = {keepalive}\n"
    )


def write_client_config(
    directory: Path,
    *,
    private_key: str,
    address: str,
    server_public_key: str,
    server_endpoint: str,
    server_allowed_ips: str = "10.8.0.1/32",
    keepalive: int = 25,
) -> Path:
    conf = directory / "wg0.conf"
    conf.write_text(
        render_client_config(
            private_key=private_key,
            address=address,
            server_public_key=server_public_key,
            server_endpoint=server_endpoint,
            server_allowed_ips=server_allowed_ips,
            keepalive=keepalive,
        ),
        encoding="utf-8",
    )
    os.chmod(conf, stat.S_IRUSR | stat.S_IWUSR)
    return conf


def interface_is_up(conf: Path, *, runner: Runner = _default_runner) -> bool:
    try:
        runner(["wg", "show", conf.stem])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


def apply_interface(conf: Path, *, runner: Runner = _default_runner) -> None:
    try:
        runner(["wg-quick", "down", str(conf)])
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    runner(["wg-quick", "up", str(conf)])


def bring_interface_down(conf: Path, *, runner: Runner = _default_runner) -> None:
    try:
        runner(["wg-quick", "down", str(conf)])
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def install_gateway_ssh_key(
    public_key: str,
    *,
    authorized_keys: Path | None = None,
    source_cidr: str = "",
) -> None:
    """Authorize the gateway key, limited to the gateway's tunnel address when known."""
    key = (public_key or "").strip()
    if not key:
        return
    path = authorized_keys or Path.home() / ".ssh" / "authorized_keys"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if key in existing:
        return
    line = f'from="{source_cidr}" {key}' if source_cidr else key
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


# Codes that mean "stop using the tunnel" rather than a transient failure.
_REVOKED_CODES = frozenset({"connectivity_revoked"})
_DISABLED_CODES = frozenset({"connectivity_disabled"})


class ConnectivitySync:
    """Keep the device tunnel in line with the control plane without flapping it.

    The agent calls :meth:`sync` on every heartbeat. Registration is repeated
    only every ``register_interval_seconds`` (or right away when the key pair
    changes), and ``wg-quick`` only runs when the rendered config changes or the
    interface is down. A revoked device tears its tunnel down and keeps it down.
    """

    def __init__(
        self,
        config_dir: Path,
        *,
        runner: Runner = _default_runner,
        register_interval_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.directory = wireguard_dir(config_dir)
        self.runner = runner
        self.register_interval_seconds = register_interval_seconds
        self.clock = clock
        self._last_attempt: float | None = None
        self._registered_key: str | None = None
        self._response: dict[str, Any] | None = None
        self.revoked = False

    @property
    def conf_path(self) -> Path:
        return self.directory / "wg0.conf"

    def _registration_due(self, public_key: str) -> bool:
        if self._last_attempt is None or public_key != self._registered_key:
            return True
        return self.clock() - self._last_attempt >= self.register_interval_seconds

    def _tear_down(self) -> None:
        bring_interface_down(self.conf_path, runner=self.runner)
        try:
            self.conf_path.unlink()
        except FileNotFoundError:
            pass

    def sync(self, client: Any, device_token: str) -> dict[str, Any] | None:
        if not wireguard_available():
            logger.info("WireGuard tools not installed; skipping device tunnel")
            return None
        try:
            private_key, public_key = ensure_keypair(self.directory, runner=self.runner)
            if self._registration_due(public_key):
                self._last_attempt = self.clock()
                try:
                    response = client.register_wireguard(device_token=device_token, public_key=public_key)
                except Exception as exc:
                    code = getattr(exc, "code", None)
                    if code in _REVOKED_CODES:
                        if not self.revoked:
                            logger.warning("WireGuard connectivity was revoked; bringing the tunnel down")
                        self.revoked = True
                        self._response = None
                        self._registered_key = public_key
                        self._tear_down()
                        return None
                    if code in _DISABLED_CODES:
                        self._registered_key = public_key
                        return None
                    raise
                self.revoked = False
                self._registered_key = public_key
                self._response = response
            if self._response is None or self.revoked:
                return None
            self._ensure_interface(private_key, self._response)
            return self._response
        except Exception:
            logger.exception("WireGuard registration failed")
            return None

    def _ensure_interface(self, private_key: str, response: dict[str, Any]) -> None:
        rendered = render_client_config(
            private_key=private_key,
            address=str(response.get("wg_address") or ""),
            server_public_key=str(response.get("server_public_key") or ""),
            server_endpoint=str(response.get("server_endpoint") or ""),
            server_allowed_ips=str(response.get("server_allowed_ips") or "10.8.0.1/32"),
            keepalive=int(response.get("persistent_keepalive") or 25),
        )
        current = self.conf_path.read_text(encoding="utf-8") if self.conf_path.is_file() else None
        changed = current != rendered
        if changed:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.conf_path.write_text(rendered, encoding="utf-8")
            os.chmod(self.conf_path, stat.S_IRUSR | stat.S_IWUSR)
        if changed or not interface_is_up(self.conf_path, runner=self.runner):
            try:
                apply_interface(self.conf_path, runner=self.runner)
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                logger.warning("Could not apply WireGuard interface: %s", exc)
        install_gateway_ssh_key(
            str(response.get("ssh_gateway_public_key") or ""),
            source_cidr=str(response.get("ssh_gateway_source") or ""),
        )


def sync_connectivity(
    client: Any,
    device_token: str,
    config_dir: Path,
    *,
    runner: Runner = _default_runner,
) -> dict[str, Any] | None:
    """One-shot register + tunnel bring-up. Long-running agents use ConnectivitySync."""
    return ConnectivitySync(config_dir, runner=runner).sync(client, device_token)
