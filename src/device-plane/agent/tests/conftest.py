"""Shared fixtures and fakes for agent tests."""

from __future__ import annotations

from typing import Any

import pytest

from edge_agent.client import AgentApiError
from edge_agent.config import AgentPaths


class FakeClient:
    """In-memory stand-in for :class:`edge_agent.client.EdgeClient`."""

    def __init__(
        self,
        server_url: str = "http://localhost:8000",
        *,
        register_response: dict[str, Any] | None = None,
        register_error: AgentApiError | None = None,
        heartbeat_error: AgentApiError | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.register_response = register_response or {
            "device_id": "device-1",
            "device_token": "dev_secret-value",
            "organization_id": "org-1",
            "name": "edge-01",
            "heartbeat_interval_seconds": 60,
        }
        self.register_error = register_error
        self.heartbeat_error = heartbeat_error
        self.register_calls: list[dict[str, Any]] = []
        self.heartbeat_calls: list[dict[str, Any]] = []
        self.enroll_request_calls: list[dict[str, Any]] = []
        self.enroll_poll_calls: list[dict[str, Any]] = []
        self.enroll_request_response: dict[str, Any] = {
            "request_id": "req-1",
            "claim_secret": "clm_secret-value",
            "status": "pending",
            "poll_interval_seconds": 1,
            "expires_at": None,
        }
        self.enroll_poll_responses: list[dict[str, Any]] = []
        self.enroll_request_error: AgentApiError | None = None
        self.health_response: dict[str, Any] = {
            "status": "ok",
            "service": "edge-platform-backend",
            "version": "0.1.0",
        }
        self.health_error: AgentApiError | None = None
        self.check_calls: list[dict[str, Any]] = []
        self.check_response: dict[str, Any] = {
            "ok": True,
            "organization_id": "org-1",
            "organization_name": "Acme Energy",
            "key_name": "Field techs",
            "key_prefix": "key_abcdef",
            "expires_at": None,
        }
        self.check_error: AgentApiError | None = None
        self.software_response: list[dict[str, Any]] = []
        self.software_error: AgentApiError | None = None
        self.software_calls: list[dict[str, Any]] = []
        self.wireguard_calls: list[dict[str, Any]] = []
        self.wireguard_error: AgentApiError | None = None
        self.wireguard_response: dict[str, Any] = {
            "status": "active",
            "provider": "wireguard",
            "wg_address": "10.8.0.12/32",
            "server_public_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "server_endpoint": "gateway.example.com:51820",
            "allowed_ips": "10.8.0.1/32",
            "persistent_keepalive": 25,
            "ssh_user": "root",
            "ssh_gateway_public_key": None,
        }

    def register(
        self,
        *,
        token: str,
        inventory: dict[str, Any],
        name: str | None,
    ) -> dict[str, Any]:
        self.register_calls.append({"token": token, "inventory": inventory, "name": name})
        if self.register_error is not None:
            raise self.register_error
        return self.register_response

    def heartbeat(self, *, device_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.heartbeat_calls.append({"device_token": device_token, "payload": payload})
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return {"device_id": "device-1", "status": "online", "heartbeat_interval_seconds": 60}

    def enroll_request(
        self,
        *,
        api_key: str,
        inventory: dict[str, Any],
        name: str | None,
    ) -> dict[str, Any]:
        self.enroll_request_calls.append({"api_key": api_key, "inventory": inventory, "name": name})
        if self.enroll_request_error is not None:
            raise self.enroll_request_error
        return self.enroll_request_response

    def enroll_poll(self, *, request_id: str, claim_secret: str) -> dict[str, Any]:
        self.enroll_poll_calls.append({"request_id": request_id, "claim_secret": claim_secret})
        if self.enroll_poll_responses:
            return self.enroll_poll_responses.pop(0)
        return {"status": "pending", "poll_interval_seconds": 1}

    def health(self) -> dict[str, Any]:
        if self.health_error is not None:
            raise self.health_error
        return self.health_response

    def check_api_key(self, *, api_key: str) -> dict[str, Any]:
        self.check_calls.append({"api_key": api_key})
        if self.check_error is not None:
            raise self.check_error
        return self.check_response

    def get_software(self, *, device_token: str) -> list[dict[str, Any]]:
        self.software_calls.append({"device_token": device_token})
        if self.software_error is not None:
            raise self.software_error
        return list(self.software_response)

    def register_wireguard(
        self,
        *,
        device_token: str,
        public_key: str,
        ssh_user: str | None = None,
        ssh_host_key: str | None = None,
        ssh_ca_public_key: str | None = None,
    ) -> dict[str, Any]:
        self.wireguard_calls.append(
            {
                "device_token": device_token,
                "public_key": public_key,
                "ssh_user": ssh_user,
                "ssh_host_key": ssh_host_key,
                "ssh_ca_public_key": ssh_ca_public_key,
            }
        )
        if self.wireguard_error is not None:
            raise self.wireguard_error
        return dict(self.wireguard_response)


@pytest.fixture
def agent_paths(tmp_path) -> AgentPaths:
    base = tmp_path / "edge-agent"
    return AgentPaths(config_path=base / "config.json", token_path=base / "device-token")
