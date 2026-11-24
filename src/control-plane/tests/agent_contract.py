"""Check control-plane answers on /api/v1/agent/* against contracts/schemas/agent.

The conftest ``client`` fixture installs :func:`check_response` as a response hook,
so every test that calls an agent route also checks the answer against the device
agent contract (contracts/device-agent-v1.md). A mismatch fails that test.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "agent"


class ContractError(AssertionError):
    """An agent route answered with a body the contract does not allow."""


@cache
def _registry() -> Registry:
    resources = []
    for path in SCHEMA_DIR.rglob("*.json"):
        if path.name == "index.json":
            continue
        resources.append((path.as_uri(), Resource.from_contents(json.loads(path.read_text(encoding="utf-8")))))
    return Registry().with_resources(resources)


@cache
def index() -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / "index.json").read_text(encoding="utf-8"))


@cache
def validator(relative: str) -> Draft202012Validator:
    """Validator for ``relative`` (e.g. ``http/register.response.json`` or ``common.json#/$defs/error``)."""
    file_part, _, pointer = relative.partition("#")
    uri = (SCHEMA_DIR / file_part).as_uri()
    schema = {"$ref": uri + (f"#{pointer}" if pointer else "")}
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def _pattern(path: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(path)) + "$")


@cache
def _operations() -> list[tuple[str, re.Pattern[str], dict[str, Any]]]:
    return [(op["method"], _pattern(op["path"]), op) for op in index()["http"]]


def operation(method: str, path: str) -> dict[str, Any] | None:
    for op_method, pattern, op in _operations():
        if op_method == method.upper() and pattern.match(path):
            return op
    return None


def errors_for(relative: str, body: Any) -> list[str]:
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in validator(relative).iter_errors(body)
    ]


def check(method: str, path: str, status: int, body: Any) -> None:
    """Raise ContractError when ``body`` breaks the contract for this route and status."""
    op = operation(method, path)
    if op is None:
        return
    if status >= 400:
        relative = index()["error"]
    else:
        responses = op["responses"]
        if str(status) not in responses:
            raise ContractError(f"{method} {path}: status {status} is not in the contract ({sorted(responses)})")
        relative = responses[str(status)]
        if relative is None:  # binary download or redirect: no JSON body
            return
    problems = errors_for(relative, body)
    if problems:
        raise ContractError(f"{method} {path} -> {status} breaks {relative}:\n  " + "\n  ".join(problems))


def check_response(response: Any) -> None:
    """Response hook for the TestClient (an httpx-style client; typed loosely, CI installs httpx2)."""
    request = response.request
    path = request.url.path
    op = operation(request.method, path) if path.startswith("/api/v1/agent/") else None
    if op is None:
        return
    status = response.status_code
    if status < 400 and op["responses"].get(str(status), "") is None:
        return  # binary download or redirect: no JSON body
    if "json" not in response.headers.get("content-type", ""):
        raise ContractError(f"{request.method} {path} -> {status}: answer is not JSON")
    response.read()
    check(request.method, path, status, response.json())
