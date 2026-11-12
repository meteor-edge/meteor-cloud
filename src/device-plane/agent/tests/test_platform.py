"""Platform ports: Linux default + heartbeat handler composition."""

from __future__ import annotations

from edge_agent.platform import combine_heartbeat_handlers, default_platform
from edge_agent.platform.ports import HostActionError, Platform


def test_default_platform_exposes_common_collectors() -> None:
    platform: Platform = default_platform()
    inventory = platform.inventory.collect()
    assert "cpu_model" in inventory
    assert "memory_mb" in inventory
    assert "hardware_model" in inventory
    metrics = platform.metrics.collect(sample_cpu=False)
    assert "temperature_c" in metrics
    caps = platform.capabilities.collect()
    for key in ("systemd", "docker", "docker_compose", "wireguard", "ssh"):
        assert key in caps


def test_combine_heartbeat_handlers_runs_in_order() -> None:
    order: list[str] = []

    def first(response: dict) -> None:
        order.append(f"wifi:{response.get('flag')}")

    def second(response: dict) -> None:
        order.append(f"actions:{response.get('flag')}")

    combine_heartbeat_handlers(first, second)({"flag": True})
    assert order == ["wifi:True", "actions:True"]


def test_host_action_error_is_public() -> None:
    assert issubclass(HostActionError, Exception)


def test_example_acme_host_actions_reboot(monkeypatch) -> None:
    """Documented stub: another OS implements HostActions, ActionSync stays unchanged."""
    from edge_agent.actions import ActionSync
    from edge_agent.platform.example_acme import AcmeHostActions

    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv, **_kwargs):
            calls.append(list(argv))

    monkeypatch.setattr("edge_agent.platform.example_acme.subprocess.Popen", FakePopen)

    class FakeClient:
        def get_actions(self, *, device_token: str):
            return [{"id": "a1", "kind": "reboot"}]

        def report_actions(self, *, device_token: str, payload: dict):
            return {"accepted": 1}

    ActionSync(FakeClient(), "tok", host=AcmeHostActions()).on_heartbeat({"actions_pending": True})
    assert calls == [["acmectl", "reboot"]]
