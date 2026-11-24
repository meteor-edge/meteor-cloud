"""The device agent contract (contracts/schemas/agent) matches the routes this API serves.

Answers themselves are checked on every test call by the ``client`` fixture hook
(tests/agent_contract.py). These tests keep the index complete and the hook honest.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from app.main import create_app
from tests.agent_contract import SCHEMA_DIR, ContractError, check, index, operation
from tests.conftest import auth_header, create_org_with_owner, create_user


def _served_agent_operations() -> set[tuple[str, str]]:
    spec = create_app().openapi()
    return {
        (method.upper(), path)
        for path, ops in spec["paths"].items()
        if path.startswith("/api/v1/agent/")
        for method in ops
    }


def test_every_agent_route_is_in_the_contract() -> None:
    contract = {(op["method"], op["path"]) for op in index()["http"]}
    served = _served_agent_operations()
    assert served - contract == set(), "agent routes missing from contracts/schemas/agent/index.json"
    assert contract - served == set(), "contract lists agent routes the API does not serve"


_LIMITS = ("maxLength", "minLength", "maximum", "minimum", "maxItems")


def _limits(schema: dict, resolve, prefix: str = "") -> dict[str, dict]:
    """Field path -> length/range limits, following $ref, allOf, nullable anyOf and arrays of objects."""
    schema = resolve(schema)
    props: dict = {}
    for part in schema.get("allOf", []):
        props.update(resolve(part).get("properties", {}))
    props.update(schema.get("properties", {}))
    out: dict[str, dict] = {}
    for name, prop in props.items():
        prop = resolve(prop)
        options = [resolve(o) for o in prop.get("anyOf", [])] or [prop]
        found = {k: float(v) for o in options for k, v in o.items() if k in _LIMITS}
        if found:
            out[prefix + name] = found
        for option in options:
            if option.get("properties") or option.get("allOf"):
                out.update(_limits(option, resolve, f"{prefix}{name}."))
            if "items" in option:
                out.update(_limits(option["items"], resolve, f"{prefix}{name}[]."))
    return out


def test_request_limits_match_the_api() -> None:
    """Every length, range and count limit the API enforces is in the request schema, with the same value.

    Otherwise an agent could pass the contract suite and still be refused by the server.
    """
    spec = create_app().openapi()
    components = spec["components"]["schemas"]

    def from_api(node: dict) -> dict:
        while "$ref" in node:
            node = components[node["$ref"].rsplit("/", 1)[-1]]
        return node

    common = json.loads((SCHEMA_DIR / "common.json").read_text(encoding="utf-8"))

    def from_contract(node: dict) -> dict:
        # Every $ref in the contract points into common.json ("../common.json#/$defs/x" or "#/$defs/x").
        while "$ref" in node:
            node = common["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        return node

    mismatches = []
    for op in index()["http"]:
        if not op["request"]:
            continue
        api_op = spec["paths"][op["path"]][op["method"].lower()]
        body = api_op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if body is None:
            continue
        contract_file = SCHEMA_DIR / op["request"]
        contract = _limits(json.loads(contract_file.read_text(encoding="utf-8")), from_contract)
        for field, limits in _limits(body, from_api).items():
            if contract.get(field) != limits:
                mismatches.append(f"{op['method']} {op['path']} {field}: API {limits}, contract {contract.get(field)}")
    assert not mismatches, "\n".join(mismatches)


def test_every_schema_file_is_valid_json_schema() -> None:
    for path in SCHEMA_DIR.rglob("*.json"):
        if path.name != "index.json":
            Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_index_points_at_existing_files() -> None:
    data = index()
    named = [data["error"].split("#")[0]]
    for op in data["http"]:
        named += [op["request"]] if op["request"] else []
        named += [r for r in op["responses"].values() if r]
    named += [topic["schema"] for topic in data["mqtt"]]
    for relative in named:
        assert (SCHEMA_DIR / relative).is_file(), relative


def test_check_rejects_a_broken_answer() -> None:
    with pytest.raises(ContractError, match="device_token"):
        check("POST", "/api/v1/agent/register", 201, {"device_id": "x"})
    with pytest.raises(ContractError, match="not in the contract"):
        check("POST", "/api/v1/agent/heartbeat", 202, {})
    with pytest.raises(ContractError):
        check("POST", "/api/v1/agent/heartbeat", 401, {"detail": "nope"})
    check("POST", "/api/v1/agent/heartbeat", 401, {"error": {"code": "invalid_device_credentials", "message": "x"}})
    assert operation("GET", "/api/v1/agent/artifacts/123/download") is not None


def _token(client: TestClient, db_session: Session) -> str:
    owner = create_user(db_session, email="contract@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    created = client.post(
        f"/api/v1/organizations/{org.id}/registration-tokens",
        json={"name": "contract"},
        headers=auth_header(client, "contract@example.com"),
    )
    assert created.status_code == 201, created.text
    return created.json()["token"]


def test_register_heartbeat_and_polls_follow_the_contract(client: TestClient, db_session: Session) -> None:
    """One pass over the device routes a new agent calls first; the hook checks each answer."""
    token = _token(client, db_session)
    registered = client.post(
        "/api/v1/agent/register",
        json={
            "token": token,
            "hostname": "contract-01",
            "mac_addresses": ["aa:bb:cc:00:00:01"],
            "agent_version": "0.4.0",
        },
        headers={"User-Agent": "meteorcli/0.4.0", "X-Meteor-Agent-Protocol": "1"},
    )
    assert registered.status_code == 201
    bearer = {"Authorization": f"Bearer {registered.json()['device_token']}"}
    heartbeat = client.post(
        "/api/v1/agent/heartbeat",
        json={
            "hostname": "contract-01",
            "capabilities": {
                "docker": True,
                "actions": ["reboot", "shutdown"],
                "deploy_kinds": ["systemd", "docker", "docker_compose"],
                "modules": {"heartbeat": {"state": "running", "restarts": 0}},
            },
            "software_report": [],
        },
        headers=bearer,
    )
    assert heartbeat.status_code == 200
    for path in ("/api/v1/agent/mqtt", "/api/v1/agent/software", "/api/v1/agent/actions", "/api/v1/agent/network/wifi"):
        assert client.get(path, headers=bearer).status_code == 200, path
    assert client.post("/api/v1/agent/actions/report", json={"results": []}, headers=bearer).status_code == 200
    assert client.post("/api/v1/agent/network/wifi/report", json={"results": []}, headers=bearer).status_code == 200
    refused = client.post("/api/v1/agent/heartbeat", json={}, headers={"Authorization": "Bearer dev_wrong"})
    assert refused.status_code == 401
