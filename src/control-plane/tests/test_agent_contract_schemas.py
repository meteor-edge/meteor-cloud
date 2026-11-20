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
