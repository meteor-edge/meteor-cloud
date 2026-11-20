"""Heartbeat capabilities (contracts/device-agent-v1.md)."""

from __future__ import annotations

from edge_agent.actions import SUPPORTED_ACTIONS
from edge_agent.capabilities import collect_capabilities
from edge_agent.mqtt import online_status_payload
from edge_agent.software import SUPPORTED_KINDS


def test_capabilities_list_actions_and_deploy_kinds() -> None:
    caps = collect_capabilities()
    assert caps["actions"] == list(SUPPORTED_ACTIONS)
    assert caps["deploy_kinds"] == ["systemd", "docker", "docker_compose"] == list(SUPPORTED_KINDS)
    for name in ("systemd", "docker", "docker_compose", "wireguard", "ssh"):
        assert isinstance(caps[name], bool)


def test_mqtt_status_reports_the_installed_version() -> None:
    from meteorcli import __version__

    assert online_status_payload()["agent_version"] == __version__
