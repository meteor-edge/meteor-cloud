"""SHA-512 crypt (``$6$``), the password hash Linux stores in /etc/shadow.

Python 3.13 removed the ``crypt`` module, so this follows Ulrich Drepper's
specification directly (https://www.akkadia.org/drepper/SHA-crypt.txt).
"""

from __future__ import annotations

import hashlib
import secrets

_ALPHABET = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_DEFAULT_ROUNDS = 5000
_MAX_SALT_LENGTH = 16
# Byte order of the 64-byte digest in the encoded hash (three bytes per four characters).
_ORDER = (
    (0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48),
    (28, 49, 7), (50, 8, 29), (9, 30, 51), (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13),
    (56, 14, 35), (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41),
)  # fmt: skip


def _repeat(block: bytes, length: int) -> bytes:
    return (block * (length // len(block) + 1))[:length]


def _digest(password: bytes, salt: bytes, rounds: int) -> bytes:
    b = hashlib.sha512(password + salt + password).digest()
    a = hashlib.sha512(password + salt + _repeat(b, len(password)))
    length = len(password)
    while length:
        a.update(b if length & 1 else password)
        length >>= 1
    a_digest = a.digest()
    p = _repeat(hashlib.sha512(password * len(password)).digest(), len(password))
    s = _repeat(hashlib.sha512(salt * (16 + a_digest[0])).digest(), len(salt))
    current = a_digest
    for index in range(rounds):
        step = hashlib.sha512()
        step.update(p if index & 1 else current)
        if index % 3:
            step.update(s)
        if index % 7:
            step.update(p)
        step.update(current if index & 1 else p)
        current = step.digest()
    return current


def _encode(digest: bytes) -> str:
    out: list[str] = []
    for first, second, third in _ORDER:
        value = digest[first] << 16 | digest[second] << 8 | digest[third]
        for _ in range(4):
            out.append(_ALPHABET[value & 0x3F])
            value >>= 6
    value = digest[63]
    for _ in range(2):
        out.append(_ALPHABET[value & 0x3F])
        value >>= 6
    return "".join(out)


def sha512_crypt(password: str, *, salt: str | None = None, rounds: int | None = None) -> str:
    """``$6$salt$hash`` for ``password``; a random 16-character salt unless one is given."""
    if salt is None:
        salt = "".join(secrets.choice(_ALPHABET) for _ in range(_MAX_SALT_LENGTH))
    salt = salt[:_MAX_SALT_LENGTH]
    if not salt or any(char not in _ALPHABET for char in salt):
        raise ValueError("salt must be 1 to 16 characters of [./0-9A-Za-z]")
    prefix = "$6$"
    if rounds is not None:
        rounds = min(max(rounds, 1000), 999_999_999)
        prefix += f"rounds={rounds}$"
    digest = _digest(password.encode(), salt.encode(), rounds or _DEFAULT_ROUNDS)
    return f"{prefix}{salt}${_encode(digest)}"
