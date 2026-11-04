"""Encrypt small device secrets at rest (WiFi PSK).

Uses Fernet (AES) with a key derived from ``DEVICE_SECRETS_KEY`` or ``APP_SECRET_KEY``.
Not a full secrets product — suitable for short shared secrets only.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


def _fernet(settings: Settings) -> Fernet:
    raw = (settings.device_secrets_key or settings.app_secret_key).encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(settings: Settings, plaintext: str) -> str:
    return _fernet(settings).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(settings: Settings, ciphertext: str) -> str:
    try:
        return _fernet(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("could not decrypt device secret") from exc
