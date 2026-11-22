"""Agent-reported capabilities (contracts/device-agent-v1.md, "Capabilities").

The heartbeat carries ``capabilities``: runtime booleans, and since v1 (additive)
``actions``, ``deploy_kinds`` and ``modules`` (the Go agent's module health). The
server stores the object as sent so unknown keys survive, but the three lists it
reads are cleaned first: a malformed value never fails a heartbeat (that would
take the device offline), it is dropped or cut to size instead.
"""

from __future__ import annotations

from typing import Any

MODULE_STATES = frozenset({"running", "starting", "waiting", "failed", "parked", "stopped"})
_MAX_KINDS = 64
_MAX_MODULES = 64
_MAX_NAME = 64
_MAX_ERROR = 500


def _kinds(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and 0 < len(item) <= _MAX_NAME and item not in out:
            out.append(item)
        if len(out) >= _MAX_KINDS:
            break
    return out


def _module(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    state = value.get("state")
    if not isinstance(state, str) or state not in MODULE_STATES:
        return None
    out: dict[str, Any] = {"state": state}
    restarts = value.get("restarts")
    if isinstance(restarts, int) and not isinstance(restarts, bool) and restarts >= 0:
        out["restarts"] = restarts
    error = value.get("last_error")
    if isinstance(error, str) and error:
        out["last_error"] = error[:_MAX_ERROR]
    since = value.get("since")
    if isinstance(since, str) and len(since) <= _MAX_NAME:
        out["since"] = since
    return out


def _modules(value: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(value):
        if not isinstance(name, str) or not 0 < len(name) <= _MAX_NAME:
            continue
        health = _module(value[name])
        if health is not None:
            out[name] = health
        if len(out) >= _MAX_MODULES:
            break
    return out


def normalize_capabilities(raw: dict[str, Any]) -> dict[str, Any]:
    """Return ``raw`` with ``actions``, ``deploy_kinds`` and ``modules`` cleaned.

    A key whose value has the wrong type is removed, so readers can trust the type of
    whatever is stored. Every other key is kept as sent.
    """
    out = dict(raw)
    for key, clean in (("actions", _kinds), ("deploy_kinds", _kinds), ("modules", _modules)):
        if key not in out:
            continue
        value = clean(out[key])
        if value is None:
            out.pop(key)
        else:
            out[key] = value
    return out
