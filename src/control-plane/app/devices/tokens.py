"""Secret token generation and hashing for fleet credentials.

Tokens use ``secrets.token_urlsafe`` with a stable prefix. Only deterministic
SHA-256 hashes are ever persisted, which allows constant-time lookup without
storing the plaintext. Secret values are never logged.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

REGISTRATION_TOKEN_PREFIX = "reg_"
DEVICE_TOKEN_PREFIX = "dev_"
# Organization-scoped enrollment API key used by the CLI to submit
# device-initiated enrollment requests.
API_KEY_PREFIX = "key_"
# Per-request secret handed to the device so it can poll for approval and
# claim its credential exactly once.
CLAIM_SECRET_PREFIX = "clm_"

# Number of random bytes for the url-safe secret body.
_SECRET_BYTES = 32
# Length of the non-secret display prefix persisted for identification.
_DISPLAY_PREFIX_LEN = 12


@dataclass(frozen=True)
class GeneratedToken:
    """A freshly generated token.

    ``plaintext`` is returned to the caller once and must never be persisted.
    ``token_hash`` and ``display_prefix`` are safe to store.
    """

    plaintext: str
    token_hash: str
    display_prefix: str


def hash_token(plaintext: str) -> str:
    """Return the deterministic SHA-256 hex digest of a token."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _display_prefix(plaintext: str) -> str:
    return plaintext[:_DISPLAY_PREFIX_LEN]


def _generate(prefix: str) -> GeneratedToken:
    plaintext = f"{prefix}{secrets.token_urlsafe(_SECRET_BYTES)}"
    return GeneratedToken(
        plaintext=plaintext,
        token_hash=hash_token(plaintext),
        display_prefix=_display_prefix(plaintext),
    )


def generate_registration_token() -> GeneratedToken:
    """Generate a new registration token (``reg_`` prefix)."""
    return _generate(REGISTRATION_TOKEN_PREFIX)


def generate_device_token() -> GeneratedToken:
    """Generate a new per-device credential (``dev_`` prefix)."""
    return _generate(DEVICE_TOKEN_PREFIX)


def generate_api_key() -> GeneratedToken:
    """Generate a new enrollment API key (``key_`` prefix)."""
    return _generate(API_KEY_PREFIX)


def generate_claim_secret() -> GeneratedToken:
    """Generate a new enrollment claim secret (``clm_`` prefix)."""
    return _generate(CLAIM_SECRET_PREFIX)
