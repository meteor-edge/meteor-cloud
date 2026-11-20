"""Load contracts/schemas/agent and check messages against it.

Kept separate from the control-plane test helper on purpose: this suite must not
import any plane's code (it also has to run against the Go agent later).
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

REPO = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO / "contracts" / "schemas" / "agent"


@cache
def index() -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / "index.json").read_text(encoding="utf-8"))


@cache
def _registry() -> Registry:
    pairs = []
    for path in SCHEMA_DIR.rglob("*.json"):
        if path.name != "index.json":
            pairs.append((path.as_uri(), Resource.from_contents(json.loads(path.read_text(encoding="utf-8")))))
    return Registry().with_resources(pairs)


@cache
def _validator(relative: str) -> Draft202012Validator:
    file_part, _, pointer = relative.partition("#")
    target = (SCHEMA_DIR / file_part).as_uri() + (f"#{pointer}" if pointer else "")
    return Draft202012Validator({"$ref": target}, registry=_registry(), format_checker=FormatChecker())


def problems(relative: str, body: Any) -> list[str]:
    """Human-readable schema violations of ``body`` against ``relative`` (empty when valid)."""
    return [
        f"{relative} {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in _validator(relative).iter_errors(body)
    ]


def _compile(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(template)) + "$")


def http_operation(method: str, path: str) -> dict[str, Any] | None:
    for op in index()["http"]:
        if op["method"] == method and _compile(op["path"]).match(path):
            return op
    return None


def mqtt_topic(topic: str) -> dict[str, Any] | None:
    for entry in index()["mqtt"]:
        if _compile(entry["topic"]).match(topic):
            return entry
    return None
