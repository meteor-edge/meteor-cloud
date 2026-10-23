"""Mask secrets before anything reaches a log file, report, or terminal."""

from __future__ import annotations

import re
import threading

_PATTERNS = (
    # PEM private keys (SSH, TLS).
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL),
    # WireGuard config lines and key=value secrets.
    re.compile(r"(?im)^(\s*PrivateKey\s*=\s*)\S+"),
    re.compile(
        r"(?i)((?:password|passwd|secret|token|private[_-]?key|access[_-]?key[_-]?id|api[_-]?key)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"
    ),
    # Bearer tokens and JWTs.
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # MeteorCloud token formats (registration, device, shell ticket, API key).
    re.compile(r"\b(?:reg|dev|sh|key)_[A-Za-z0-9_-]{12,}"),
    re.compile(r"([?&]ticket=)[^&\s]+"),
)
MASK = "***"


class Redactor:
    """Masks known secret values plus common secret formats."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, *values: str | None) -> None:
        with self._lock:
            for value in values:
                if value and len(value) >= 6:
                    self._values.add(value)

    def __call__(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            text = text.replace(value, MASK)
        for pattern in _PATTERNS:
            if pattern.groups:
                text = pattern.sub(lambda m: m.group(1) + MASK, text)
            else:
                text = pattern.sub(MASK, text)
        return text


redact = Redactor()
