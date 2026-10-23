"""Device-side WireGuard: generate keys on the device, never upload the private key."""

from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import socket
import stat
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("edge_agent")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

# WireGuard handshakes every 2 minutes while traffic flows (keepalive keeps it flowing).
STALE_HANDSHAKE_SECONDS = 180


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
    allowed_ips: str = "10.8.0.1/32",
    keepalive: int = 25,
) -> tuple[Path, bool]:
    conf = directory / "wg0.conf"
    address_line = address if "/" in address else f"{address}/32"
    content = (
        "[Interface]\n"
        f"Address = {address_line}\n"
        f"PrivateKey = {private_key}\n"
        "\n[Peer]\n"
        f"PublicKey = {server_public_key}\n"
        f"Endpoint = {server_endpoint}\n"
        f"AllowedIPs = {allowed_ips}\n"
        f"PersistentKeepalive = {keepalive}\n"
    )
    previous = conf.read_text(encoding="utf-8") if conf.is_file() else None
    changed = previous != content
    if changed:
        conf.write_text(content, encoding="utf-8")
        os.chmod(conf, stat.S_IRUSR | stat.S_IWUSR)
    return conf, changed


def apply_interface(
    conf: Path,
    *,
    runner: Runner = _default_runner,
    config_changed: bool = True,
) -> None:
    if not config_changed:
        try:
            runner(["wg", "show", conf.stem])
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    try:
        runner(["wg-quick", "down", str(conf)])
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    runner(["wg-quick", "up", str(conf)])


def _peer_value(runner: Runner, interface: str, field: str, peer: str) -> str:
    """One peer's column from `wg show <interface> <field>` ("<public key>\t<value>" lines)."""
    for line in runner(["wg", "show", interface, field]).stdout.splitlines():
        key, _, value = line.partition("\t")
        if key == peer:
            return value.strip()
    return ""


def _resolve_ipv4(host: str) -> str:
    return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]


def refresh_endpoint(
    interface: str,
    *,
    server_public_key: str,
    server_endpoint: str,
    runner: Runner = _default_runner,
    resolve: Callable[[str], str] = _resolve_ipv4,
    now: Callable[[], float] = time.time,
) -> None:
    """Point the tunnel at the server's current IP when the endpoint is a DNS name.

    WireGuard looks the name up only when the tunnel starts. If the server moved to a new IP,
    handshakes stop; then look the name up again and update the peer in place.
    """
    host, _, port = server_endpoint.rpartition(":")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return  # a fixed IP: nothing to look up
    except ValueError:
        pass
    if not host or not port:
        return
    handshake = _peer_value(runner, interface, "latest-handshakes", server_public_key)
    if handshake.isdigit() and now() - int(handshake) < STALE_HANDSHAKE_SECONDS:
        return
    try:
        target = f"{resolve(host)}:{port}"
    except OSError as exc:
        logger.warning("Could not resolve WireGuard server %s: %s", host, exc)
        return
    current = _peer_value(runner, interface, "endpoints", server_public_key)
    if current != target:
        runner(["wg", "set", interface, "peer", server_public_key, "endpoint", target])
        logger.info("WireGuard server %s now at %s (was %s)", host, target, current or "unset")


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
        server_public_key = str(response.get("server_public_key") or "")
        server_endpoint = str(response.get("server_endpoint") or "")
        conf, changed = write_client_config(
            wireguard_dir(config_dir),
            private_key=private_key,
            address=str(response.get("wg_address") or ""),
            server_public_key=server_public_key,
            server_endpoint=server_endpoint,
            allowed_ips=str(response.get("allowed_ips") or "10.8.0.1/32"),
            keepalive=int(response.get("persistent_keepalive") or 25),
        )
        try:
            apply_interface(conf, runner=runner, config_changed=changed)
            refresh_endpoint(conf.stem, server_public_key=server_public_key,
                             server_endpoint=server_endpoint, runner=runner)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("Could not apply WireGuard interface: %s", exc)
        install_gateway_ssh_key(str(response.get("ssh_gateway_public_key") or ""))
        return response
    except Exception as exc:
        if getattr(exc, "code", None) == "connectivity_disabled":
            logger.info("Device connectivity is disabled; skipping WireGuard")
            return None
        logger.exception("WireGuard registration failed")
        return None
