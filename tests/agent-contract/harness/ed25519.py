"""Test-only Ed25519 signer (RFC 8032), so the fake control plane can sign desired software.

Slow pure Python, fine for a handful of signatures. Never use outside tests.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_GY = 4 * pow(5, _P - 2, _P) % _P


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _x_from_y(y: int) -> int:
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return _P - x if x & 1 else x


_G = (_x_from_y(_GY), _GY, 1, _x_from_y(_GY) * _GY % _P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % _P
    bb = (y1 + x1) * (y2 + x2) % _P
    cc = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, point):
    result = (0, 1, 1, 0)
    while s:
        if s & 1:
            result = _add(result, point)
        point = _add(point, point)
        s >>= 1
    return result


def _encode(point) -> bytes:
    zi = _inv(point[2])
    x, y = point[0] * zi % _P, point[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed: bytes) -> bytes:
    return _encode(_mul(_expand(seed)[0], _G))


def sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _expand(seed)
    public = _encode(_mul(scalar, _G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    big_r = _encode(_mul(r, _G))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % _L
    return big_r + ((r + k * scalar) % _L).to_bytes(32, "little")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


DESIRED_CONTEXT = b"meteorcloud-desired-v1\n"
_SIGNED_FIELDS = (
    "workload_key", "kind", "release_id", "release_version", "artifact_id", "artifact_sha256", "revision", "spec",
)


def sign_desired(seed: bytes, device_id: str, item: dict[str, Any]) -> str:
    """Signature over a desired-software item, as docs/fleet/signed-updates.md defines it."""
    payload = {"device_id": device_id, **{key: item.get(key) for key in _SIGNED_FIELDS}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return b64url(sign(seed, DESIRED_CONTEXT + canonical))
