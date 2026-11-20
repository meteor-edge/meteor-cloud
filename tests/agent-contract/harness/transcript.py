"""Recordings: what an agent and the control plane said to each other, minus what varies.

A recording (recordings/<scenario>.json) is the normalized transcript of one scenario:
every HTTP exchange in order and every distinct MQTT message. Values that change per
run or per host (ids, secrets, timestamps, inventory readings) become placeholders
such as ``<uuid:1>`` or ``<device_token>``, so two runs of a correct agent produce the
same recording. ``Materializer`` turns placeholders back into fresh values for replay.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings"

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")
VERSION_RE = re.compile(r"meteorcli/\d+\.\d+\.\d+\S*")

# Request fields read from the host: their values differ per machine, their presence does not.
INVENTORY_KEYS = frozenset(
    {
        "serial_number", "mac_addresses", "hostname", "os_name", "os_version", "kernel_version",
        "architecture", "hardware_model", "cpu_model", "cpu_cores", "memory_mb", "local_ip", "agent_version",
    }
)
RECORDED_HEADERS = ("authorization", "user-agent", "x-meteor-agent-protocol", "x-meteorcli-redirect")


@dataclass
class Normalizer:
    """Replaces run-specific values with stable placeholders. One instance per scenario run."""

    secrets: dict[str, str] = field(default_factory=dict)  # value -> name
    literals: dict[str, str] = field(default_factory=dict)  # value -> placeholder
    uuids: dict[str, str] = field(default_factory=dict)

    def secret(self, name: str, value: str) -> None:
        self.secrets[value] = f"<{name}>"

    def literal(self, placeholder: str, value: Any) -> None:
        self.literals[str(value)] = placeholder

    def text(self, value: str) -> str:
        if value in self.secrets:
            return self.secrets[value]
        if value in self.literals:
            return self.literals[value]
        if value.startswith("-----BEGIN"):
            return "<pem>"
        if TIMESTAMP_RE.match(value):
            return "<timestamp>"
        for known, name in sorted(self.secrets.items(), key=lambda kv: -len(kv[0])):
            value = value.replace(known, name)
        for known, placeholder in sorted(self.literals.items(), key=lambda kv: -len(kv[0])):
            if len(known) > 3:
                value = value.replace(known, placeholder)
        value = VERSION_RE.sub("meteorcli/<version>", value)
        return UUID_RE.sub(self._uuid, value)

    def _uuid(self, match: re.Match[str]) -> str:
        found = match.group(0)
        if found not in self.uuids:
            self.uuids[found] = f"<uuid:{len(self.uuids) + 1}>"
        return self.uuids[found]

    def value(self, value: Any, *, inventory: bool = False) -> Any:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if inventory and key in INVENTORY_KEYS:
                    out[key] = "<inventory>"
                else:
                    out[key] = self.value(item)
            return out
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and str(value) in self.literals:
            return self.literals[str(value)]
        return value

    def headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {name: self.text(headers[name]) for name in RECORDED_HEADERS if name in headers}

    def mqtt_payload(self, topic_suffix: str, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        if topic_suffix == "metrics":
            # Host readings vary; app metrics come from the simulated device and are kept as sent.
            kept = {k: v for k, v in payload.items() if k.startswith("app.")}
            return {"timestamp": self.value(payload.get("timestamp")), "<host readings>": True, **dict(sorted(kept.items()))}
        normalized = self.value(payload)
        if topic_suffix == "status":
            for key, placeholder in (("uptime_seconds", "<number>"), ("agent_version", "<version>")):
                if key in normalized:
                    normalized[key] = placeholder
        return normalized


def binary_body(raw: bytes) -> dict[str, str]:
    return {"base64": base64.b64encode(raw).decode()}


@dataclass
class Materializer:
    """Turns recorded placeholders into fresh concrete values for a replay."""

    literals: dict[str, Any]  # placeholder -> concrete value (server URL, broker port ...)
    concrete: dict[str, str] = field(default_factory=dict)  # placeholder -> generated value

    def placeholder_for(self, value: str) -> str | None:
        for placeholder, concrete in self.concrete.items():
            if concrete == value:
                return placeholder
        return None

    def text(self, value: str) -> Any:
        if value in self.literals:
            return self.literals[value]
        if value == "<timestamp>":
            return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return re.sub(r"<[a-z_]+(:\d+)?>", lambda m: str(self._fresh(m.group(0))), value)

    def _fresh(self, placeholder: str) -> Any:
        if placeholder in self.literals:
            return self.literals[placeholder]
        if placeholder not in self.concrete:
            if placeholder.startswith("<uuid:"):
                self.concrete[placeholder] = str(uuid.uuid4())
            else:
                self.concrete[placeholder] = f"{placeholder.strip('<>')}_{secrets.token_hex(8)}"
        return self.concrete[placeholder]

    def value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def load(scenario: str) -> dict[str, Any]:
    return json.loads((RECORDINGS / f"{scenario}.json").read_text(encoding="utf-8"))


def save(scenario: str, transcript: dict[str, Any]) -> Path:
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    path = RECORDINGS / f"{scenario}.json"
    path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
