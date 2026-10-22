"""Device-side WireGuard: generate keys on the device, never upload the private key."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
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


def write_client_config(
    directory: Path,
    *,
    private_key: str,
    address: str,
    server_public_key: str,
    server_endpoint: str,
    keepalive: int = 25,
) -> Path:
    conf = directory / "wg0.conf"
    address_line = address if "/" in address else f"{address}/32"
    conf.write_text(
        "[Interface]\n"
        f"Address = {address_line}\n"
        f"PrivateKey = {private_key}\n"
        "\n[Peer]\n"
        f"PublicKey = {server_public_key}\n"
        f"Endpoint = {server_endpoint}\n"
        "AllowedIPs = 10.8.0.1/32\n"
        f"PersistentKeepalive = {keepalive}\n",
        encoding="utf-8",
    )
    os.chmod(conf, stat.S_IRUSR | stat.S_IWUSR)
    return conf


def apply_interface(conf: Path, *, runner: Runner = _default_runner) -> None:
    try:
        runner(["wg-quick", "down", str(conf)])
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    runner(["wg-quick", "up", str(conf)])


def install_gateway_ssh_key(public_key: str, *, authorized_keys: Path | None = None) -> None:
    key = (public_key or "").strip()
    if not key:
        return
    path = authorized_keys or Path.home() / ".ssh" / "authorized_keys"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if key in existing:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(key + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def sync_connectivity(
    client: Any,
    device_token: str,
    config_dir: Path,
    *,
    runner: Runner = _default_runner,
) -> dict[str, Any] | None:
    """Register this device's public key and bring the tunnel up. Private key stays local."""
    if not wireguard_available():
        logger.info("WireGuard tools not installed; skipping device tunnel")
        return None
    try:
        private_key, public_key = ensure_keypair(wireguard_dir(config_dir), runner=runner)
        response = client.register_wireguard(device_token=device_token, public_key=public_key)
        write_client_config(
            wireguard_dir(config_dir),
            private_key=private_key,
            address=str(response.get("wg_address") or ""),
            server_public_key=str(response.get("server_public_key") or ""),
            server_endpoint=str(response.get("server_endpoint") or ""),
            keepalive=int(response.get("persistent_keepalive") or 25),
        )
        try:
            apply_interface(wireguard_dir(config_dir) / "wg0.conf", runner=runner)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("Could not apply WireGuard interface: %s", exc)
        install_gateway_ssh_key(str(response.get("ssh_gateway_public_key") or ""))
        return response
    except Exception:
        logger.exception("WireGuard registration failed")
        return None
