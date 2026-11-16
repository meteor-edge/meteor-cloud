"""Verify signed desired software before downloading or applying it.

The control plane signs each desired-software item with the organization's Ed25519 key
(``app/signing/service.py``). The agent pins the organization's public keys the first
time it fetches them and afterwards trusts a new key only when a pinned key endorsed it
(a key rotation). An item whose signature does not verify is refused, so a stolen
database dump without the encryption key, a tampered artifact store or a rewritten
network response cannot make the device install something the organization did not sign.
See docs/fleet/signed-updates.md.

Ed25519 verification uses ``cryptography`` when it is installed and otherwise a small
pure-Python implementation of RFC 8032, so the agent keeps its single dependency.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("edge_agent")

DESIRED_CONTEXT = b"meteorcloud-desired-v1\n"
ENDORSEMENT_CONTEXT = b"meteorcloud-key-endorsement-v1\n"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def desired_payload(device_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """The fields a signature covers. Must match ``signed_desired_payload`` on the server."""
    return {
        "device_id": device_id,
        "workload_key": item.get("workload_key"),
        "kind": item.get("kind"),
        "release_id": item.get("release_id"),
        "release_version": item.get("release_version"),
        "artifact_id": item.get("artifact_id"),
        "artifact_sha256": item.get("artifact_sha256"),
        "revision": item.get("revision"),
        "spec": item.get("spec"),
    }


# --- Ed25519 verification (RFC 8032, section 5.1.7) -----------------------

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
assert _GX is not None
_G = (_GX, _GY, 1, _GX * _GY % _P)

Point = tuple[int, int, int, int]


def _add(a: Point, b: Point) -> Point:
    pa = (a[1] - a[0]) * (b[1] - b[0]) % _P
    pb = (a[1] + a[0]) * (b[1] + b[0]) % _P
    pc = 2 * a[3] * b[3] * _D % _P
    pd = 2 * a[2] * b[2] % _P
    e, f, g, h = pb - pa, pd - pc, pd + pc, pb + pa
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point: Point) -> Point:
    result: Point = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _decompress(raw: bytes) -> Point | None:
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _verify_pure(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return _verify_pure(public_key, message, signature)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True


# --- pinned keys and manifest checks --------------------------------------


def credential_fingerprint(device_token: str) -> str:
    """Short hash of the device credential; pins are discarded when it changes."""
    return hashlib.sha256(device_token.encode()).hexdigest()[:32]


class ManifestVerifier:
    """Checks desired-software items against the organization keys this device pinned.

    ``fetch_keys`` returns the server's key list, or None when the server does not sign
    yet (an older control plane): then unsigned items are accepted until keys exist.
    ``credential_fingerprint`` ties the pin file to the device token that fetched it so
    a late save after re-enrollment cannot restore another organization's pins.
    """

    def __init__(
        self,
        store: Path,
        device_id: str,
        fetch_keys: Callable[[], list[dict[str, Any]] | None],
        credential_fingerprint: str | None = None,
    ) -> None:
        self.store = store
        self.device_id = device_id
        self.fetch_keys = fetch_keys
        self.credential_fingerprint = credential_fingerprint
        self._server_signs: bool | None = None

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        if data.get("device_id") not in (None, "", self.device_id):
            return {}
        # Require an exact credential match when one is configured so a late save
        # from a previous enrollment cannot restore that enrollment's pins.
        if self.credential_fingerprint and data.get("credential_fp") != self.credential_fingerprint:
            return {}
        return {str(k): str(v) for k, v in (data.get("keys") or {}).items()}

    def _save(self, keys: dict[str, str]) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"keys": keys, "device_id": self.device_id}
        if self.credential_fingerprint:
            payload["credential_fp"] = self.credential_fingerprint
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.store)

    def _refresh(self, pinned: dict[str, str]) -> dict[str, str]:
        """Pin every key on first use; later only keys endorsed by a pinned key."""
        fetched = self.fetch_keys()
        if fetched is None:
            self._server_signs = False
            return pinned
        self._server_signs = True
        if not pinned:
            pinned = {
                str(k["key_id"]): str(k["public_key"]) for k in fetched if k.get("key_id") and k.get("public_key")
            }
            if pinned:
                logger.warning("Pinned %d organization signing key(s) on first use: %s", len(pinned), ", ".join(pinned))
                self._save(pinned)
            return pinned
        changed = True
        added = False
        while changed:
            changed = False
            for key in fetched:
                key_id, public_key = str(key.get("key_id") or ""), str(key.get("public_key") or "")
                endorser = pinned.get(str(key.get("endorsed_by") or ""))
                if not key_id or key_id in pinned or not endorser or not key.get("endorsement"):
                    continue
                message = ENDORSEMENT_CONTEXT + canonical_json({"key_id": key_id, "public_key": public_key})
                try:
                    endorsed = ed25519_verify(b64url_decode(endorser), message, b64url_decode(str(key["endorsement"])))
                except ValueError:
                    endorsed = False
                if endorsed:
                    pinned[key_id] = public_key
                    logger.info("Pinned rotated signing key %s (endorsed by %s)", key_id, key.get("endorsed_by"))
                    changed = added = True
        if added:
            self._save(pinned)
        return pinned

    def check(self, item: dict[str, Any]) -> str | None:
        """None when the item may be applied, else why it is refused."""
        try:
            return self._check(item)
        except Exception as exc:  # noqa: BLE001 — a key fetch failed; refuse and retry next cycle
            logger.warning("Could not check the signature of %s: %s", item.get("workload_key"), exc)
            return f"Could not check the update signature ({exc}); will retry."

    def _check(self, item: dict[str, Any]) -> str | None:
        pinned = self._load()
        if not pinned:
            pinned = self._refresh(pinned)
        signature, key_id = item.get("signature"), item.get("key_id")
        if not signature or not key_id:
            if not pinned and self._server_signs is False:
                return None  # older control plane that does not sign yet
            return "Desired software is not signed; refusing to install it."
        if key_id not in pinned:
            pinned = self._refresh(pinned)
        public_key = pinned.get(str(key_id))
        if public_key is None:
            return f"Desired software is signed by an untrusted key ({key_id}); refusing to install it."
        message = DESIRED_CONTEXT + canonical_json(desired_payload(self.device_id, item))
        try:
            valid = ed25519_verify(b64url_decode(public_key), message, b64url_decode(str(signature)))
        except ValueError:
            valid = False
        if not valid:
            return "Desired software signature does not verify; refusing to install it."
        return None
