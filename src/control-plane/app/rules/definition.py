"""Validation of rule definitions (contracts/rules-v1.md).

This mirrors the data plane's Go validator (``internal/rules/dsl``), which stays the
authority: the engine re-validates every rule it loads and refuses one it cannot
compile. Both are tested against ``contracts/rules-v1.examples.json``. Validation here
gives the API immediate, path-addressed errors without a round trip to the data plane.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
SEVERITIES = ("info", "warning", "critical")
SCOPES = ("device", "group", "device_type", "organization", "platform")
NODE_TYPES = ("comparison", "all", "any", "not", "duration", "count_within", "consecutive", "aggregate", "absence")
OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "not_in", "exists", "not_exists")
FUNCTIONS = ("avg", "min", "max", "sum", "count", "delta", "rate")
ACTION_TYPES = ("alert", "webhook", "command", "notification", "platform_event")
DEFAULT_STREAM = "meteor.device.metrics"

MAX_DEPTH = 8
MAX_NODES = 64
MIN_WINDOW = 1.0
MAX_WINDOW = 24 * 3600.0
MAX_ACTIONS = 10
MAX_LIST_ITEMS = 100
MAX_DEVICES = 1000

TOP_KEYS = {
    "schema_version",
    "name",
    "description",
    "enabled",
    "severity",
    "scope",
    "source",
    "condition",
    "resolve",
    "cooldown",
    "alerting",
    "actions",
    "late_tolerance",
}
NODE_KEYS = {
    "type",
    "field",
    "operator",
    "value",
    "values",
    "min",
    "max",
    "conditions",
    "condition",
    "duration",
    "window",
    "min_count",
    "count",
    "function",
    "streams",
}
# Keys each node type may carry besides "type".
ALLOWED = {
    "comparison": {"field", "operator", "value", "values", "min", "max"},
    "all": {"conditions"},
    "any": {"conditions"},
    "not": {"condition"},
    "duration": {"duration", "condition"},
    "count_within": {"window", "min_count", "condition"},
    "consecutive": {"count", "condition"},
    "aggregate": {"function", "field", "window", "operator", "value", "min", "max"},
    "absence": {"duration", "streams"},
}
SCOPE_KEYS = {"type", "device_ids", "group_id", "device_type_id", "labels"}
ACTION_KEYS = {"type", "on", "severity", "integration_id", "command", "params", "timeout"}

_REF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FIELD_SEG = re.compile(r"^[A-Za-z0-9_-]+$")
_STREAM_SEG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DURATION = re.compile(r"^(?:(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h))+$")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
_UNIT = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


@dataclass
class Problem:
    path: str
    message: str


class RuleDefinitionError(ValueError):
    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(f"{p.path}: {p.message}" if p.path else p.message for p in problems))


def parse_duration(value: Any) -> float | None:
    """Seconds for a Go-style duration string ("90s", "1h30m"), else None."""
    if not isinstance(value, str) or not _DURATION.match(value):
        return None
    return sum(float(n) * _UNIT[u] for n, u in _DURATION_PART.findall(value))


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _is_scalar(v: Any) -> bool:
    return _is_number(v) or isinstance(v, bool) or (isinstance(v, str) and len(v) <= 256)


def _valid_field(f: Any) -> bool:
    return isinstance(f, str) and 0 < len(f) <= 128 and all(_FIELD_SEG.match(s) for s in f.split("."))


def _valid_stream(s: Any) -> bool:
    if not isinstance(s, str) or not 0 < len(s) <= 128:
        return False
    parts = s.split(".")
    return len(parts) <= 8 and all(_STREAM_SEG.match(p) for p in parts)


def _valid_ref(v: Any) -> bool:
    return isinstance(v, str) and bool(_REF.match(v))


class _Validator:
    def __init__(self, allow_platform: bool) -> None:
        self.allow_platform = allow_platform
        self.problems: list[Problem] = []
        self.nodes = 0

    def add(self, path: str, message: str) -> None:
        self.problems.append(Problem(path, message))

    def unknown(self, path: str, obj: dict[str, Any], allowed: set[str]) -> None:
        for key in sorted(set(obj) - allowed):
            self.add(f"{path}.{key}" if path else key, "unknown field")

    def window(self, path: str, value: Any) -> None:
        seconds = parse_duration(value)
        if seconds is None or not MIN_WINDOW <= seconds <= MAX_WINDOW:
            self.add(path, "must be a duration between 1s and 24h0m0s")

    def rule(self, d: Any) -> None:
        if not isinstance(d, dict):
            self.add("", "a rule must be a JSON object")
            return
        self.unknown("", d, TOP_KEYS)
        if d.get("schema_version") != SCHEMA_VERSION or isinstance(d.get("schema_version"), bool):
            self.add("schema_version", f"must be {SCHEMA_VERSION}")
        name = d.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            self.add("name", "is required and at most 200 characters")
        if "description" in d and (not isinstance(d["description"], str) or len(d["description"]) > 2000):
            self.add("description", "is at most 2000 characters")
        if "enabled" in d and not isinstance(d["enabled"], bool):
            self.add("enabled", "must be true or false")
        if "severity" in d and d["severity"] not in SEVERITIES:
            self.add("severity", "must be info, warning or critical")
        self.scope(d.get("scope"))
        if "source" in d:
            src = d["source"]
            if not isinstance(src, dict):
                self.add("source", "must be an object")
            else:
                self.unknown("source", src, {"streams"})
                self.streams("source.streams", src.get("streams", []))
        if d.get("condition") is None:
            self.add("condition", "is required")
        else:
            self.node("condition", d["condition"], 1)
        if d.get("resolve") is not None:
            self.node("resolve", d["resolve"], 1)
            if _contains(d["resolve"], "absence"):
                self.add("resolve", "absence cannot be used in a resolve condition")
        for key in ("cooldown", "late_tolerance"):
            if key in d:
                seconds = parse_duration(d[key])
                if seconds is None or seconds > MAX_WINDOW:
                    self.add(key, "must be a duration between 0s and 24h0m0s")
        if "alerting" in d:
            self.alerting(d["alerting"])
        self.actions(d.get("actions", []))

    def scope(self, s: Any) -> None:
        if not isinstance(s, dict):
            self.add("scope", "is required")
            return
        self.unknown("scope", s, SCOPE_KEYS)
        t = s.get("type")
        if t == "device":
            ids = s.get("device_ids")
            if not isinstance(ids, list) or not 0 < len(ids) <= MAX_DEVICES:
                self.add("scope.device_ids", f"needs 1 to {MAX_DEVICES} device ids")
            else:
                for i, v in enumerate(ids):
                    if not _valid_ref(v):
                        self.add(f"scope.device_ids[{i}]", "is not a valid id")
        elif t == "group":
            if not _valid_ref(s.get("group_id")):
                self.add("scope.group_id", "is required for a group scope")
        elif t == "device_type":
            if not _valid_ref(s.get("device_type_id")):
                self.add("scope.device_type_id", "is required for a device_type scope")
        elif t == "organization":
            pass
        elif t == "platform":
            if not self.allow_platform:
                self.add("scope.type", "platform rules are for platform administrators only")
        else:
            self.add("scope.type", "must be device, group, device_type, organization or platform")
        if t != "device" and s.get("device_ids"):
            self.add("scope.device_ids", "applies only to a device scope")
        if t != "group" and s.get("group_id"):
            self.add("scope.group_id", "applies only to a group scope")
        if t != "device_type" and s.get("device_type_id"):
            self.add("scope.device_type_id", "applies only to a device_type scope")
        labels = s.get("labels")
        if labels is not None:
            if not isinstance(labels, dict) or len(labels) > 20:
                self.add("scope.labels", "at most 20 labels")
            elif any(
                not isinstance(k, str) or not 0 < len(k) <= 64 or not isinstance(v, str) or len(v) > 256
                for k, v in labels.items()
            ):
                self.add("scope.labels", "label keys are 1-64 characters, values are strings of at most 256")

    def streams(self, path: str, items: Any) -> None:
        if not isinstance(items, list):
            self.add(path, "must be a list")
            return
        if len(items) > 10:
            self.add(path, "at most 10 streams")
        for i, s in enumerate(items):
            if not _valid_stream(s):
                self.add(f"{path}[{i}]", "is not a valid stream name")

    def node(self, path: str, n: Any, depth: int) -> None:
        if not isinstance(n, dict):
            self.add(path, "must be a condition object")
            return
        self.nodes += 1
        if self.nodes == MAX_NODES + 1:
            self.add(path, f"the rule has more than {MAX_NODES} condition nodes")
        if depth > MAX_DEPTH:
            self.add(path, f"conditions are nested deeper than {MAX_DEPTH} levels")
            return
        t = n.get("type")
        if t is None:
            self.add(f"{path}.type", "is required")
            return
        if t not in NODE_TYPES:
            self.add(f"{path}.type", f'unknown node type "{t}"')
            return
        for key in sorted(set(n) - NODE_KEYS):
            self.add(f"{path}.{key}", "unknown field")
        for key in sorted((set(n) & NODE_KEYS) - ALLOWED[t] - {"type"}):
            self.add(f"{path}.{key}", f"does not apply to a {t} node")

        def child() -> None:
            self.node(f"{path}.condition", n.get("condition"), depth + 1)

        if t == "comparison":
            self.comparison(path, n)
        elif t in ("all", "any"):
            kids = n.get("conditions")
            if not isinstance(kids, list) or not 1 <= len(kids) <= 20:
                self.add(f"{path}.conditions", "needs 1 to 20 conditions")
            else:
                for i, c in enumerate(kids):
                    self.node(f"{path}.conditions[{i}]", c, depth + 1)
        elif t == "not":
            child()
        elif t == "duration":
            self.window(f"{path}.duration", n.get("duration"))
            child()
        elif t == "count_within":
            self.window(f"{path}.window", n.get("window"))
            mc = n.get("min_count")
            if not isinstance(mc, int) or isinstance(mc, bool) or not 1 <= mc <= 100000:
                self.add(f"{path}.min_count", "must be between 1 and 100000")
            child()
        elif t == "consecutive":
            c = n.get("count")
            if not isinstance(c, int) or isinstance(c, bool) or not 1 <= c <= 10000:
                self.add(f"{path}.count", "must be between 1 and 10000")
            child()
        elif t == "aggregate":
            self.window(f"{path}.window", n.get("window"))
            if n.get("function") not in FUNCTIONS:
                self.add(f"{path}.function", "must be avg, min, max, sum, count, delta or rate")
            if not _valid_field(n.get("field")):
                self.add(f"{path}.field", "must be a dotted field name")
            op = n.get("operator")
            if op in ("eq", "ne", "gt", "gte", "lt", "lte"):
                if not _is_number(n.get("value")):
                    self.add(f"{path}.value", "must be a number")
            elif op == "between":
                self.between(path, n)
            else:
                self.add(f"{path}.operator", "must be eq, ne, gt, gte, lt, lte or between for an aggregate")
        elif t == "absence":
            self.window(f"{path}.duration", n.get("duration"))
            if "streams" in n:
                self.streams(f"{path}.streams", n["streams"])

    def comparison(self, path: str, n: dict[str, Any]) -> None:
        if not _valid_field(n.get("field")):
            self.add(f"{path}.field", "must be a dotted field name")
        op = n.get("operator")
        if op in ("eq", "ne"):
            if not _is_scalar(n.get("value")):
                self.add(f"{path}.value", "must be a number, string or boolean")
        elif op in ("gt", "gte", "lt", "lte"):
            if not _is_number(n.get("value")):
                self.add(f"{path}.value", "must be a number")
        elif op == "between":
            self.between(path, n)
        elif op in ("in", "not_in"):
            vals = n.get("values")
            if not isinstance(vals, list) or not 1 <= len(vals) <= MAX_LIST_ITEMS:
                self.add(f"{path}.values", f"needs 1 to {MAX_LIST_ITEMS} values")
            else:
                for i, v in enumerate(vals):
                    if not _is_scalar(v):
                        self.add(f"{path}.values[{i}]", "must be a number, string or boolean")
        elif op in ("exists", "not_exists"):
            if "value" in n:
                self.add(f"{path}.value", f"does not apply to {op}")
        elif op is None:
            self.add(f"{path}.operator", "is required")
        else:
            self.add(f"{path}.operator", f'unknown operator "{op}"')
        if op not in ("in", "not_in") and n.get("values"):
            self.add(f"{path}.values", "applies only to in and not_in")
        if op != "between" and ("min" in n or "max" in n):
            self.add(f"{path}.min", "min and max apply only to between")

    def between(self, path: str, n: dict[str, Any]) -> None:
        lo, hi = n.get("min"), n.get("max")
        if not _is_number(lo) or not _is_number(hi):
            self.add(path, "between needs min and max")
        elif lo > hi:
            self.add(f"{path}.min", "must not be greater than max")
        if "value" in n:
            self.add(f"{path}.value", "between uses min and max, not value")

    def alerting(self, a: Any) -> None:
        if not isinstance(a, dict):
            self.add("alerting", "must be an object")
            return
        self.unknown("alerting", a, {"mode", "max_active"})
        mode, mx = a.get("mode", "single"), a.get("max_active", 0)
        if mode == "single":
            if isinstance(mx, int) and mx > 1:
                self.add("alerting.max_active", "applies only to per_event mode")
        elif mode == "per_event":
            if not isinstance(mx, int) or isinstance(mx, bool) or not 0 <= mx <= 100:
                self.add("alerting.max_active", "must be between 1 and 100")
        else:
            self.add("alerting.mode", "must be single or per_event")

    def actions(self, acts: Any) -> None:
        if not isinstance(acts, list):
            self.add("actions", "must be a list")
            return
        if len(acts) > MAX_ACTIONS:
            self.add("actions", f"at most {MAX_ACTIONS} actions")
        alerts = 0
        for i, a in enumerate(acts):
            p = f"actions[{i}]"
            if not isinstance(a, dict):
                self.add(p, "must be an object")
                continue
            self.unknown(p, a, ACTION_KEYS)
            t = a.get("type")
            if t == "alert":
                alerts += 1
                if "severity" in a and a["severity"] not in SEVERITIES:
                    self.add(f"{p}.severity", "must be info, warning or critical")
            elif t in ("webhook", "notification"):
                if not _valid_ref(a.get("integration_id")):
                    self.add(f"{p}.integration_id", "is required")
            elif t == "command":
                c = a.get("command")
                if not isinstance(c, str) or not 0 < len(c) <= 64 or not _valid_field(c):
                    self.add(f"{p}.command", "is required (1-64 characters of a-z 0-9 . _ -)")
            elif t == "platform_event":
                pass
            else:
                self.add(f"{p}.type", "must be alert, webhook, command, notification or platform_event")
            for on in a.get("on", []) if isinstance(a.get("on", []), list) else ["?"]:
                if on not in ("firing", "resolved"):
                    self.add(f"{p}.on", "entries must be firing or resolved")
            if "params" in a and (not isinstance(a["params"], dict) or len(json.dumps(a["params"])) > 4096):
                self.add(f"{p}.params", "must be a JSON object of at most 4 KiB")
            if "timeout" in a:
                seconds = parse_duration(a["timeout"])
                if seconds is None or seconds > 60:
                    self.add(f"{p}.timeout", "must be between 0s and 60s")
        if alerts > 1:
            self.add("actions", "at most one alert action")


def _contains(n: Any, node_type: str) -> bool:
    if not isinstance(n, dict):
        return False
    if n.get("type") == node_type:
        return True
    kids = n.get("conditions") if isinstance(n.get("conditions"), list) else []
    return any(_contains(c, node_type) for c in kids) or _contains(n.get("condition"), node_type)


def validate_definition(definition: Any, *, allow_platform: bool = False) -> dict[str, Any]:
    """Return the definition unchanged if valid, else raise RuleDefinitionError with every problem."""
    v = _Validator(allow_platform)
    v.rule(definition)
    if v.problems:
        raise RuleDefinitionError(v.problems)
    return definition


def severity_of(definition: dict[str, Any]) -> str:
    return definition.get("severity") or "warning"


def condition_hash(definition: dict[str, Any]) -> str:
    """Fingerprint of what shapes evaluation state, for display (the engine computes its own)."""
    shape = {k: definition.get(k) for k in ("condition", "resolve", "scope", "source", "alerting")}
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:24]
