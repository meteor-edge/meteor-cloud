"""Device-side WireGuard: generate keys on the device, never upload the private key."""

from __future__ import annotations

import errno
import ipaddress
import logging
import os
import random
import shutil
import socket
import stat
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("edge_agent")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

# WireGuard handshakes every 2 minutes while traffic flows (keepalive keeps it flowing).
STALE_HANDSHAKE_SECONDS = 180
_PRIVILEGED_WG = frozenset({"show", "set", "addconf", "syncconf"})

# Registration is a refresh, not a keepalive: the tunnel runs from the local wg0.conf between calls.
REGISTER_REFRESH_SECONDS = 900
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 1800
# Revoked or key mismatch: an operator has to act (re-enroll); check back now and then.
BLOCKED_RETRY_SECONDS = 1800
_BLOCKED_CODES = frozenset({"connectivity_revoked", "wireguard_key_changed"})
SSH_HOST_KEY_PATHS = (
    Path("/etc/ssh/ssh_host_ed25519_key.pub"),
    Path("/etc/ssh/ssh_host_ecdsa_key.pub"),
    Path("/etc/ssh/ssh_host_rsa_key.pub"),
)
_SSH_HOST_KEY_TYPES = frozenset({"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"})


@dataclass
class ConnectivityState:
    """When to call the registration API next. Lives for one `run` loop; restarts register at once."""

    next_register_at: float = 0.0
    failures: int = 0
    blocked: str | None = None
    clock: Callable[[], float] = time.monotonic
    jitter: Callable[[float, float], float] = field(default=random.uniform)

    def due(self) -> bool:
        return self.clock() >= self.next_register_at

    def succeeded(self) -> None:
        self.failures = 0
        self.blocked = None
        self.next_register_at = self.clock() + REGISTER_REFRESH_SECONDS * self.jitter(0.8, 1.2)

    def failed(self) -> float:
        """Bounded exponential backoff with jitter. Returns the delay."""
        self.failures += 1
        delay = min(RETRY_BASE_SECONDS * 2 ** (self.failures - 1), RETRY_MAX_SECONDS) * self.jitter(0.5, 1.0)
        self.next_register_at = self.clock() + delay
        return delay

    def block(self, code: str) -> None:
        self.failures = 0
        self.blocked = code
        self.next_register_at = self.clock() + BLOCKED_RETRY_SECONDS * self.jitter(0.8, 1.2)


def _elevate_wireguard(args: Sequence[str]) -> list[str]:
    """Create/change wg0 needs CAP_NET_ADMIN. Use passwordless sudo when not root."""
    cmd = list(args)
    if os.geteuid() == 0 or not cmd:
        return cmd
    if cmd[0] == "wg-quick" or (cmd[0] == "wg" and len(cmd) > 1 and cmd[1] in _PRIVILEGED_WG):
        resolved = shutil.which(cmd[0])
        if resolved:
            cmd[0] = resolved
        return ["sudo", "-n", *cmd]
    return cmd


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(_elevate_wireguard(args), check=True, capture_output=True, text=True, timeout=30)


def _try_chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except PermissionError:
        return
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EACCES):
            raise


def wireguard_available() -> bool:
    return shutil.which("wg") is not None and shutil.which("wg-quick") is not None


def sshd_available() -> bool:
    return Path("/usr/sbin/sshd").is_file() or shutil.which("sshd") is not None


def wireguard_dir(config_dir: Path) -> Path:
    return config_dir / "wireguard"


def ensure_keypair(directory: Path, *, runner: Runner = _default_runner) -> tuple[str, str]:
    """Return (private_key, public_key). Private key is written mode 0600."""
    directory.mkdir(parents=True, exist_ok=True)
    _try_chmod(directory, stat.S_IRWXU)
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
    _try_chmod(public_path, stat.S_IRUSR | stat.S_IWUSR)
    return private_key, public_key


def _write_secret(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    _try_chmod(path, stat.S_IRUSR | stat.S_IWUSR)


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
        _try_chmod(conf, stat.S_IRUSR | stat.S_IWUSR)
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


def local_ssh_user() -> str:
    """User the browser terminal should SSH as (the account that owns authorized_keys)."""
    sudo_user = (os.environ.get("SUDO_USER") or "").strip()
    if os.geteuid() == 0 and sudo_user and sudo_user != "root":
        return sudo_user
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, OSError):
        return (os.environ.get("USER") or "root").strip() or "root"


def ssh_authorized_keys_path(username: str | None = None) -> Path:
    name = username or local_ssh_user()
    try:
        import pwd

        return Path(pwd.getpwnam(name).pw_dir) / ".ssh" / "authorized_keys"
    except (ImportError, KeyError, OSError):
        return Path.home() / ".ssh" / "authorized_keys"


# Comments that mark the lines this agent manages in authorized_keys; nothing else is touched.
GATEWAY_KEY_COMMENT = "meteorcloud-gateway"
CA_KEY_COMMENT = "meteorcloud-ca"
# Comment of the gateway key as generated by Ansible and the docs (older agents wrote it verbatim).
_LEGACY_GATEWAY_COMMENT = "meteorcloud-shell-gateway"


def _key_of(line: str) -> str | None:
    """"<type> <base64>" from an authorized_keys line (with or without options), or None."""
    parts = line.split()
    for index, part in enumerate(parts[:-1]):
        if part.startswith(("ssh-", "ecdsa-", "sk-")):
            return f"{part} {parts[index + 1]}"
    return None


def _is_gateway_line(line: str) -> bool:
    return line.split()[-1:] in ([GATEWAY_KEY_COMMENT], [_LEGACY_GATEWAY_COMMENT])


def _is_ca_line(line: str) -> bool:
    return line.split()[-1:] == [CA_KEY_COMMENT]


def trusted_ssh_ca(authorized_keys: Path | None = None) -> str | None:
    """The SSH user CA this device's authorized_keys trusts (reported so the server can use certificates)."""
    path = authorized_keys or ssh_authorized_keys_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return next((_key_of(line) for line in lines if _is_ca_line(line)), None)


def sync_authorized_keys(
    *,
    gateway_key: str = "",
    gateway_key_retired: bool = False,
    ca_key: str = "",
    principal: str = "",
    authorized_keys: Path | None = None,
) -> str | None:
    """Keep the server's lines in authorized_keys current; returns the trusted CA ("<type> <base64>").

    Lines are added or replaced, and removed only when the server says so (gateway key retired),
    so an older server or a partial response never locks the operator out.
    """
    path = authorized_keys or ssh_authorized_keys_path()
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    lines = list(existing)
    gateway = _key_of(gateway_key or "")
    if gateway_key_retired:
        lines = [line for line in lines if not _is_gateway_line(line)]
    elif gateway and not any(_key_of(line) == gateway for line in lines):
        lines = [line for line in lines if not _is_gateway_line(line)]
        lines.append(f"{gateway} {GATEWAY_KEY_COMMENT}")
    ca = _key_of(ca_key or "")
    if ca and principal and '"' not in principal:
        wanted = f'cert-authority,principals="{principal}" {ca} {CA_KEY_COMMENT}'
        if wanted not in lines:
            lines = [line for line in lines if not _is_ca_line(line)] + [wanted]
    if lines != existing:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".meteorcloud")
        temporary.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
    return next((_key_of(line) for line in lines if _is_ca_line(line)), None)


def install_gateway_ssh_key(public_key: str, *, authorized_keys: Path | None = None) -> None:
    """Older entry point: add the shared gateway key."""
    sync_authorized_keys(gateway_key=public_key, authorized_keys=authorized_keys)


def local_ssh_host_key(paths: Sequence[Path] = SSH_HOST_KEY_PATHS) -> str | None:
    """This device's sshd host public key ("<type> <base64>"), so the server can pin it."""
    for path in paths:
        try:
            parts = path.read_text(encoding="utf-8").split()
        except OSError:
            continue
        if len(parts) >= 2 and parts[0] in _SSH_HOST_KEY_TYPES:
            return f"{parts[0]} {parts[1]}"
    return None


def _peer_from_config(conf: Path) -> tuple[str, str]:
    """(server public key, endpoint) from the [Peer] section of the local wg0.conf."""
    public_key = endpoint = ""
    for line in conf.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "PublicKey":
            public_key = value.strip()
        elif name.strip() == "Endpoint":
            endpoint = value.strip()
    return public_key, endpoint


def keep_tunnel_up(directory: Path, *, runner: Runner = _default_runner) -> None:
    """Bring wg0 up from the last config and follow gateway DNS changes. No API call.

    Lets the tunnel come back after a reboot or outage even while the control plane is unreachable.
    """
    conf = directory / "wg0.conf"
    if not conf.is_file():
        return
    try:
        apply_interface(conf, runner=runner, config_changed=False)
        server_public_key, server_endpoint = _peer_from_config(conf)
        if server_public_key and server_endpoint:
            refresh_endpoint(
                conf.stem, server_public_key=server_public_key, server_endpoint=server_endpoint, runner=runner
            )
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError, OSError) as exc:
        logger.debug("WireGuard interface not up yet: %s", exc)


def _tear_down(directory: Path, *, runner: Runner) -> None:
    conf = directory / "wg0.conf"
    if not conf.is_file():
        return
    try:
        runner(["wg-quick", "down", str(conf)])
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError):
        pass


def sync_connectivity(
    client: Any,
    device_token: str,
    config_dir: Path,
    *,
    runner: Runner = _default_runner,
    state: ConnectivityState | None = None,
) -> dict[str, Any] | None:
    """Register this device's public key and bring the tunnel up. Private key stays local.

    With ``state`` (the run loop), the API is called only when a refresh or retry is due;
    in between, the tunnel is kept up from the local config.
    """
    if not wireguard_available():
        logger.info("WireGuard tools not installed; skipping device tunnel")
        return None
    directory = wireguard_dir(config_dir)
    if state is not None and not state.due():
        if state.blocked is None:
            keep_tunnel_up(directory, runner=runner)
        return None
    try:
        private_key, public_key = ensure_keypair(directory, runner=runner)
        reported_ca = trusted_ssh_ca()
        response = client.register_wireguard(
            device_token=device_token,
            public_key=public_key,
            ssh_user=local_ssh_user(),
            ssh_host_key=local_ssh_host_key(),
            ssh_ca_public_key=reported_ca,
        )
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == "connectivity_disabled":
            logger.info("Device connectivity is disabled; skipping WireGuard")
            if state is not None:
                state.block(code)
            return None
        if code in _BLOCKED_CODES:
            if state is None or state.blocked != code:
                logger.warning(
                    "WireGuard access refused (%s): %s Tunnel stopped; an operator must re-enroll this device.",
                    code,
                    getattr(exc, "message", exc),
                )
            _tear_down(directory, runner=runner)
            if state is not None:
                state.block(code)
            return None
        if isinstance(exc, PermissionError):
            logger.warning(
                "WireGuard config is not writable (%s). "
                "If you previously used sudo meteorcli, run: sudo chown -R \"$USER\" ~/.config/meteorcli",
                exc,
            )
            return None
        delay = state.failed() if state is not None else 0.0
        if code is not None:
            logger.warning(
                "WireGuard registration failed (%s); keeping the current tunnel, retrying in %.0fs", code, delay
            )
        else:
            logger.exception("WireGuard registration failed")
        keep_tunnel_up(directory, runner=runner)
        return None

    server_public_key = str(response.get("server_public_key") or "")
    server_endpoint = str(response.get("server_endpoint") or "")
    try:
        conf, changed = write_client_config(
            directory,
            private_key=private_key,
            address=str(response.get("wg_address") or ""),
            server_public_key=server_public_key,
            server_endpoint=server_endpoint,
            allowed_ips=str(response.get("allowed_ips") or "10.8.0.1/32"),
            keepalive=int(response.get("persistent_keepalive") or 25),
        )
    except PermissionError as exc:
        logger.warning(
            "WireGuard config is not writable (%s). "
            "If you previously used sudo meteorcli, run: sudo chown -R \"$USER\" ~/.config/meteorcli",
            exc,
        )
        return None
    try:
        apply_interface(conf, runner=runner, config_changed=changed)
        refresh_endpoint(conf.stem, server_public_key=server_public_key,
                         server_endpoint=server_endpoint, runner=runner)
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as exc:
        logger.warning(
            "Could not apply WireGuard interface: %s. "
            "meteorcli run does not need root; installcli.sh adds passwordless sudo for wg/wg-quick.",
            exc,
        )
    try:
        trusted_ca = sync_authorized_keys(
            gateway_key=str(response.get("ssh_gateway_public_key") or ""),
            gateway_key_retired=bool(response.get("ssh_gateway_key_retired")),
            ca_key=str(response.get("ssh_ca_public_key") or ""),
            principal=str(response.get("ssh_principal") or ""),
        )
    except OSError as exc:
        logger.warning("Could not update authorized_keys for browser SSH: %s", exc)
        trusted_ca = reported_ca
    if trusted_ca and trusted_ca != reported_ca:
        # Tell the server now that this device accepts certificates, not at the next refresh.
        try:
            client.register_wireguard(
                device_token=device_token,
                public_key=public_key,
                ssh_user=local_ssh_user(),
                ssh_host_key=local_ssh_host_key(),
                ssh_ca_public_key=trusted_ca,
            )
        except Exception as exc:  # reported again on the next refresh
            logger.info("Could not report SSH certificate support yet: %s", exc)
    if state is not None:
        state.succeeded()
    return response
