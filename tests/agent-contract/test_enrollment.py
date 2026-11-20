"""Getting an identity: registration token, and enrollment API key with approval."""

from __future__ import annotations

import uuid

from harness.fake_cp import Reply
from harness.script import ORG_ID, error, registered


def test_register(contract, agent) -> None:
    """register, then register again: the second call proves itself with the current device token."""
    device_id = str(uuid.uuid4())
    contract.script(
        "POST", "/api/v1/agent/register",
        registered(contract, device_id), registered(contract, device_id, token="rotated_device_token"),
    )
    token = contract.secret("registration_token")

    assert agent.register(contract.plane.url, token) == 0
    assert agent.register(contract.plane.url, token) == 0

    first, second = contract.plane.calls("POST", "/api/v1/agent/register")
    assert first.request.body["token"] == token and "device_token" not in first.request.body
    assert second.request.body["device_token"] == first.reply.body["device_token"]
    contract.finish()


def test_enroll(contract, agent) -> None:
    """test (key check), request-token, then claim: pending, rate limited, approved."""
    device_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    api_key = contract.secret("api_key")
    claim_secret = contract.secret("claim_secret")
    contract.script("GET", "/api/v1/health", Reply(200, {"status": "ok", "service": "meteorcloud", "version": "test"}))
    contract.script("POST", "/api/v1/agent/enroll/check", Reply(200, {
        "ok": True, "organization_id": ORG_ID, "organization_name": "Contract Org",
        "key_name": "Field techs", "key_prefix": "key_contract", "expires_at": None,
    }))
    contract.script("POST", "/api/v1/agent/enroll/request", Reply(201, {
        "request_id": request_id, "claim_secret": claim_secret, "status": "pending",
        "poll_interval_seconds": 10, "expires_at": None,
    }))
    contract.script(
        "POST", "/api/v1/agent/enroll/poll",
        Reply(200, {"status": "pending", "poll_interval_seconds": 10}),
        error(429, "rate_limited", "Too many polls.", **{"Retry-After": "15"}),
        Reply(200, {
            "status": "approved", "poll_interval_seconds": 10, "device_id": device_id,
            "device_token": contract.secret("device_token"), "organization_id": ORG_ID,
            "name": "contract-device", "heartbeat_interval_seconds": 60, "mqtt": None,
        }),
    )

    assert agent.configure(contract.plane.url, api_key) == 0
    assert agent.test() == 0
    assert agent.request_token() == 0
    assert agent.claim() == 0

    assert agent.device_id is not None
    for poll in contract.plane.calls("POST", "/api/v1/agent/enroll/poll"):
        assert "authorization" not in poll.request.headers
    contract.finish()
