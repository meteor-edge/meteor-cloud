"""WireGuard connectivity provider (key format + AllowedIPs)."""

from __future__ import annotations

import base64
import binascii
import re

_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42,44}={0,2}$")


class WireGuardConnectivityProvider:
    name = "wireguard"

    def validate_public_key(self, public_key: str) -> str:
        key = (public_key or "").strip()
        if not _WG_KEY_RE.match(key):
            raise ValueError("WireGuard public key must be a 32-byte base64 value.")
        try:
            raw = base64.b64decode(key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("WireGuard public key is not valid base64.") from exc
        if len(raw) != 32:
            raise ValueError("WireGuard public key must decode to 32 bytes.")
        return key

    def client_allowed_ips(self, address: str) -> str:
        host = address.split("/", 1)[0]
        return f"{host}/32"
