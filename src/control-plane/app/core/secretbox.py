"""Encrypt short secrets the server must hold for a while (a Wi-Fi password on its way to a device).

AES-256-GCM. The key is ``SECRETS_ENCRYPTION_KEY`` when set, else derived from
``APP_SECRET_KEY``; either way through HKDF with its own label, so it is never the key
that signs sessions. The context (which row the secret belongs to) is authenticated,
so a ciphertext copied to another row does not decrypt.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings

_VERSION = b"\x01"
_NONCE_BYTES = 12
_INFO = b"meteorcloud secretbox v1"


class SecretBoxError(Exception):
    """The ciphertext cannot be read: wrong key, wrong row, or damaged."""


def _key(settings: Settings) -> bytes:
    source = settings.secrets_encryption_key or settings.app_secret_key
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(source.encode())


def seal(settings: Settings, plaintext: str, *, context: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return _VERSION + nonce + AESGCM(_key(settings)).encrypt(nonce, plaintext.encode(), context.encode())


def unseal(settings: Settings, sealed: bytes, *, context: str) -> str:
    if len(sealed) <= 1 + _NONCE_BYTES or sealed[:1] != _VERSION:
        raise SecretBoxError("unknown format")
    nonce, ciphertext = sealed[1 : 1 + _NONCE_BYTES], sealed[1 + _NONCE_BYTES :]
    try:
        return AESGCM(_key(settings)).decrypt(nonce, ciphertext, context.encode()).decode()
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise SecretBoxError("cannot decrypt") from exc
