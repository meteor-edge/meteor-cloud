"""Plain data types returned by the SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    # Python 3.10 accepts at most 6 fractional digits.
    if "." in text:
        head, _, rest = text.partition(".")
        digits = ""
        for ch in rest:
            if not ch.isdigit():
                break
            digits += ch
        text = f"{head}.{digits[:6].ljust(6, '0')}{rest[len(digits):]}"
    return datetime.fromisoformat(text)


def format_time(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.astimezone()  # naive datetimes are local time, as elsewhere in Python
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    """One stored message: telemetry, an event or an inference result."""

    id: str
    stream: str
    kind: str
    type: str
    data: dict[str, Any]
    time: datetime
    received_at: datetime | None
    organization_id: str
    application: str | None = None
    application_id: str | None = None
    device_id: str | None = None
    environment: str | None = None
    seq: int | None = None
    schema_version: int = 1

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Event:
        return cls(
            id=raw["id"],
            stream=raw["stream"],
            kind=raw.get("kind", "telemetry"),
            type=raw.get("type", raw["stream"]),
            data=raw.get("data") or {},
            time=parse_time(raw["time"]),  # type: ignore[arg-type]
            received_at=parse_time(raw.get("received_at")),
            organization_id=raw.get("organization_id", ""),
            application=raw.get("application") or None,
            application_id=raw.get("application_id") or None,
            device_id=raw.get("device_id") or None,
            environment=raw.get("environment") or None,
            seq=raw.get("seq"),
            schema_version=raw.get("schema_version", 1),
        )


@dataclass(frozen=True)
class PublishResult:
    """Outcome for one published message.

    ``status`` is ``stored`` (durably saved), ``duplicate`` (this id was already
    saved; nothing was written twice) or ``rejected`` (see ``error_code``).
    """

    id: str
    status: str
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("stored", "duplicate")


@dataclass(frozen=True)
class Page:
    """One page of historical events. Pass ``next_cursor`` back to get the next page."""

    events: list[Event]
    next_cursor: str | None = None

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)


@dataclass(frozen=True)
class Bucket:
    """One time bucket of an aggregate. ``value`` is None for an empty bucket."""

    start: datetime
    value: float | None
    count: int


#: Command states. A command ends in SUCCEEDED, FAILED or TIMEOUT.
FINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "TIMEOUT"})


@dataclass(frozen=True)
class Command:
    id: str
    name: str
    payload: dict[str, Any]
    state: str
    application: str
    device_id: str | None = None
    issued_by: str | None = None
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    completed_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def done(self) -> bool:
        return self.state in FINAL_STATES

    @property
    def succeeded(self) -> bool:
        return self.state == "SUCCEEDED"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Command:
        return cls(
            id=raw["id"],
            name=raw.get("name", ""),
            payload=raw.get("payload") or {},
            state=str(raw.get("state", "")).upper(),
            application=raw.get("application", ""),
            device_id=raw.get("device_id") or None,
            issued_by=raw.get("issued_by") or None,
            attempts=raw.get("attempts", 0),
            result=raw.get("result"),
            error=raw.get("error") or None,
            created_at=parse_time(raw.get("created_at")),
            expires_at=parse_time(raw.get("expires_at")),
            completed_at=parse_time(raw.get("completed_at")),
            raw=raw,
        )


@dataclass(frozen=True)
class Identity:
    """Who the token says this application is, and what it may do."""

    organization_id: str
    application: str
    application_id: str
    environment: str | None
    device_id: str | None
    permissions: dict[str, Any]
    expires_at: datetime | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Identity:
        return cls(
            organization_id=raw.get("organization_id", ""),
            application=raw.get("application", ""),
            application_id=raw.get("application_id", ""),
            environment=raw.get("environment") or None,
            device_id=raw.get("device_id") or None,
            permissions=raw.get("permissions") or {},
            expires_at=parse_time(raw.get("expires_at")),
        )
