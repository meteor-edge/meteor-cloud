"""Stream names and stream permission patterns shared with the data plane.

The data plane enforces the same rules (``contracts/data-plane-v1.md``). A
stream name is dot separated: ``energy.power``. A pattern may use ``*`` for
exactly one segment and a final ``**`` for one or more segments.
"""

from __future__ import annotations

import re

MAX_STREAM_LENGTH = 128
MAX_SEGMENTS = 8
RESERVED_PREFIX = "meteor"
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class StreamPatternError(ValueError):
    """A stream name or pattern does not follow the contract."""


def validate_stream_pattern(pattern: str) -> str:
    value = pattern.strip()
    if not value or len(value) > MAX_STREAM_LENGTH:
        raise StreamPatternError(f"stream pattern {pattern!r} must be 1-{MAX_STREAM_LENGTH} characters")
    parts = value.split(".")
    if len(parts) > MAX_SEGMENTS:
        raise StreamPatternError(f"stream pattern {pattern!r} has more than {MAX_SEGMENTS} segments")
    if value == "**":
        raise StreamPatternError("a bare '**' grants every stream; list the stream prefixes instead")
    for index, part in enumerate(parts):
        if part == "*":
            continue
        if part == "**":
            if index != len(parts) - 1:
                raise StreamPatternError(f"'**' may only end a pattern: {pattern!r}")
            continue
        if not _SEGMENT.match(part):
            raise StreamPatternError(
                f"stream pattern {pattern!r}: segments use a-z, 0-9, '_' or '-' and start with a letter or digit"
            )
    if parts[0] == RESERVED_PREFIX:
        raise StreamPatternError(f"streams under '{RESERVED_PREFIX}.' are reserved for the platform")
    return value


def stream_matches(pattern: str, stream: str) -> bool:
    p_parts = pattern.split(".")
    s_parts = stream.split(".")
    for index, part in enumerate(p_parts):
        if part == "**":
            return len(s_parts) > index
        if index >= len(s_parts):
            return False
        if part != "*" and part != s_parts[index]:
            return False
    return len(p_parts) == len(s_parts)
