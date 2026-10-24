"""Short-lived OpenSSH user certificates signed by the platform's SSH user CA.

Devices trust the CA only for their own principal (``cert-authority,principals=...``
in authorized_keys), so a certificate opens one device for a few minutes and
names who asked for it (the key ID shows up in the device's auth log).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from app.core.exceptions import ValidationAppError

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]

# Key IDs end up in device logs: keep them to one printable line.
_UNSAFE_KEY_ID = re.compile(r"[^A-Za-z0-9@._:+=/-]")


def run_keygen(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(list(args), capture_output=True, check=False, timeout=15)


def safe_key_id(value: str) -> str:
    return _UNSAFE_KEY_ID.sub("_", value)[:200] or "meteorcloud"


def generate_key(directory: Path, *, runner: Runner = run_keygen) -> Path:
    """A throw-away ed25519 key in ``directory`` (the caller removes it)."""
    key = directory / "id_ed25519"
    result = runner(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "meteorcloud-session", "-f", str(key)])
    if result.returncode != 0:
        raise ValidationAppError("shell_certificate_failed", "Could not create a session key.")
    return key


def sign_public_key(
    public_key: Path,
    *,
    ca_file: str,
    key_id: str,
    principal: str,
    ttl_seconds: int,
    runner: Runner = run_keygen,
) -> Path:
    """Sign ``public_key`` (a .pub file); returns the ``-cert.pub`` path next to it.

    Valid from a minute ago (clock skew) for ``ttl_seconds``; only a PTY is allowed:
    no agent, port or X11 forwarding.
    """
    if not ca_file:
        raise ValidationAppError("shell_certificate_failed", "The SSH certificate authority is not configured.")
    result = runner(
        [
            "ssh-keygen",
            "-q",
            "-s",
            ca_file,
            "-I",
            safe_key_id(key_id),
            "-n",
            principal,
            "-V",
            f"-1m:+{int(ttl_seconds)}s",
            "-O",
            "clear",
            "-O",
            "permit-pty",
            str(public_key),
        ]
    )
    certificate = public_key.with_name(public_key.name.removesuffix(".pub") + "-cert.pub")
    if result.returncode != 0 or not certificate.is_file():
        raise ValidationAppError("shell_certificate_failed", "Could not sign an SSH certificate for this session.")
    return certificate
