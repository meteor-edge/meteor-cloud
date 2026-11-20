"""Getting an identity: registration token, and enrollment API key with approval.

The transcripts are limited to the enrollment routes: an agent that starts working in the
background once enrolled (the Go agent) sends heartbeats meanwhile, which are checked against
the contract but are not part of these scenarios.
"""

from __future__ import annotations

import time
import uuid

import pytest

from harness.fake_cp import Reply
from harness.script import ORG_ID, error, heartbeat, registered

REGISTER = [("POST", "/api/v1/agent/register")]
ENROLL = [
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/agent/enroll/check"),
    ("POST", "/api/v1/agent/enroll/request"),
    ("POST", "/api/v1/agent/enroll/poll"),
]
HEARTBEAT = ("POST", "/api/v1/agent/heartbeat")


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
    contract.finish(routes=REGISTER)


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
    contract.finish(routes=ENROLL)


@pytest.mark.requires("server_heartbeat_interval")
def test_heartbeat(contract, agent) -> None:
    """register, online within 5 s, then the server's heartbeat interval (5 s) is used."""
    device_id = str(uuid.uuid4())
    contract.script("POST", "/api/v1/agent/register", registered(contract, device_id))
    beat = heartbeat(device_id)
    contract.script("POST", "/api/v1/agent/heartbeat",
                    Reply(200, {**beat.body, "heartbeat_interval_seconds": 5}), beat)
    contract.script("GET", "/api/v1/agent/mqtt", Reply(200, {"enabled": False, "config_version": None}))

    assert agent.register(contract.plane.url, contract.secret("registration_token")) == 0
    registered_at = time.monotonic()
    assert agent.run(heartbeats=2) == 0

    first, second = contract.plane.calls(*HEARTBEAT)[:2]
    assert first.request.bearer is not None and first.request.bearer == second.request.bearer
    gap = second.at - first.at
    assert 4.0 <= gap <= 8.0, f"second heartbeat {gap:.1f} s after the first, server asked for 5 s"
    assert first.at - registered_at < 5.0, "the device was not online within 5 s of register"
    modules = first.request.body["capabilities"]["modules"]
    assert {"heartbeat", "identity", "socket"} <= set(modules)
    contract.finish(routes=[*REGISTER, HEARTBEAT])


@pytest.mark.requires("background_enrollment")
def test_enroll_reboot(contract, agent) -> None:
    """request-token, reboot while pending, approved meanwhile: online with no command run."""
    device_id = str(uuid.uuid4())
    contract.script("POST", "/api/v1/agent/enroll/request", Reply(201, {
        "request_id": str(uuid.uuid4()), "claim_secret": contract.secret("claim_secret"), "status": "pending",
        "poll_interval_seconds": 10, "expires_at": None,
    }))
    # Still pending while the first agent runs (it polls 10 s after the request at the earliest).
    contract.script("POST", "/api/v1/agent/enroll/poll", Reply(200, {"status": "pending", "poll_interval_seconds": 10}))
    contract.script("POST", "/api/v1/agent/heartbeat", heartbeat(device_id))
    contract.script("GET", "/api/v1/agent/mqtt", Reply(200, {"enabled": False, "config_version": None}))

    assert agent.configure(contract.plane.url, contract.secret("api_key")) == 0
    assert agent.request_token() == 0
    assert agent.stop() == 0
    # Off and not enrolled: then the operator approves.
    assert agent.device_id is None and not contract.plane.calls(*HEARTBEAT)
    assert not contract.plane.calls("POST", "/api/v1/agent/enroll/poll"), "polled before the restart"
    contract.script("POST", "/api/v1/agent/enroll/poll", Reply(200, {
        "status": "approved", "poll_interval_seconds": 10, "device_id": device_id,
        "device_token": contract.secret("device_token"), "organization_id": ORG_ID,
        "name": "contract-device", "heartbeat_interval_seconds": 60, "mqtt": None,
    }))
    started_at = time.monotonic()
    agent.start()
    assert agent.run(heartbeats=1) == 0

    assert agent.device_id is not None  # the recording supplies the id in replay mode
    first = contract.plane.calls(*HEARTBEAT)[0]
    assert started_at <= first.at < started_at + 5.0, "not online within 5 s of the restart"
    contract.finish(routes=[("POST", "/api/v1/agent/enroll/request"), ("POST", "/api/v1/agent/enroll/poll"), HEARTBEAT])
