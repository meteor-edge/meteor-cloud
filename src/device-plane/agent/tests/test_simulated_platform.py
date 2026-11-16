"""Simulated device platform: profiles, inventory shape, metrics, capabilities and power actions."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from edge_agent.platform import selection
from edge_agent.platform.ports import Platform
from edge_agent.platform.simulated import (
    ProfileError,
    SimulatedCapabilities,
    SimulatedHostActions,
    SimulatedInventory,
    SimulatedMetrics,
    SimulatedPlatform,
    _boot_main,
    available_profiles,
    load_profile,
    simulated_identity,
)

INVENTORY_KEYS = {
    "serial_number",
    "mac_addresses",
    "hostname",
    "os_name",
    "os_version",
    "kernel_version",
    "architecture",
    "hardware_model",
    "cpu_model",
    "cpu_cores",
    "memory_mb",
    "local_ip",
}
CAPABILITY_KEYS = {"systemd", "docker", "docker_compose", "wireguard", "ssh"}
REAL_INVENTORY = {key: f"real-{key}" for key in INVENTORY_KEYS} | {"mac_addresses": ["aa:bb:cc:dd:ee:ff"]}
REAL_METRICS = {
    "cpu_percent": 50.0,
    "memory_percent": 25.0,
    "memory_used_bytes": 1,
    "memory_total_bytes": 4,
    "disk_percent": 10.0,
    "disk_used_bytes": 1,
    "disk_total_bytes": 10,
    "temperature_c": None,
}


@pytest.fixture(autouse=True)
def _fresh_platform(monkeypatch: pytest.MonkeyPatch):
    for name in ("METEORCLI_PLATFORM", "METEORCLI_DEVICE_PROFILE", "METEORCLI_SIM_ID"):
        monkeypatch.delenv(name, raising=False)
    selection.reset_active_platform()
    yield
    selection.reset_active_platform()


def test_the_first_profiles_ship_with_the_agent() -> None:
    assert {"rpi4", "rpi5", "nuc", "jetson-orin-nano"} <= set(available_profiles())


@pytest.mark.parametrize("name", available_profiles())
def test_every_profile_produces_the_full_shape(name: str) -> None:
    platform: Platform = SimulatedPlatform(name, seed="unit")
    inventory = platform.inventory.collect()
    assert INVENTORY_KEYS <= set(inventory)
    assert inventory["simulated"] is True
    assert inventory["device_profile"] == name
    assert inventory["serial_number"].startswith("SIM-")
    profile = load_profile(name)
    for field in ("hardware_model", "cpu_model", "cpu_cores", "memory_mb", "architecture"):
        assert inventory[field] == profile["inventory"][field]

    metrics = platform.metrics.collect(sample_cpu=False)
    assert isinstance(metrics["temperature_c"], float)
    assert CAPABILITY_KEYS <= set(platform.capabilities.collect())


def test_profiles_carry_real_board_models() -> None:
    assert load_profile("rpi5")["inventory"]["hardware_model"].startswith("Raspberry Pi 5")
    assert load_profile("jetson-orin-nano")["inventory"]["hardware_model"].startswith("NVIDIA Jetson Orin Nano")


def test_identity_is_stable_per_instance_and_distinct_between_instances() -> None:
    assert simulated_identity("rpi5", "a") == simulated_identity("rpi5", "a")
    assert simulated_identity("rpi5", "a") != simulated_identity("rpi5", "b")
    _, mac = simulated_identity("rpi5", "a")
    assert mac.startswith("02:")  # locally administered: never a real vendor's address


def test_host_facts_stay_real() -> None:
    inventory = SimulatedInventory(load_profile("rpi5"), "x", real=lambda: dict(REAL_INVENTORY)).collect()
    assert inventory["hostname"] == "real-hostname"
    assert inventory["local_ip"] == "real-local_ip"
    assert inventory["os_name"] == "real-os_name"
    assert inventory["mac_addresses"] != REAL_INVENTORY["mac_addresses"]


def test_profile_from_a_file(tmp_path: Path) -> None:
    custom = load_profile("nuc") | {"name": "my-box"}
    path = tmp_path / "my-box.json"
    path.write_text(json.dumps(custom), encoding="utf-8")
    assert SimulatedPlatform(str(path), seed="x").inventory.collect()["device_profile"] == "my-box"


@pytest.mark.parametrize("value", ["no-such-board", "../etc/passwd"])
def test_unknown_profiles_are_refused(value: str) -> None:
    with pytest.raises(ProfileError):
        load_profile(value)


def test_incomplete_profile_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"inventory": {"hardware_model": "x"}}), encoding="utf-8")
    with pytest.raises(ProfileError, match="cpu_model"):
        load_profile(str(path))


def test_metrics_are_sized_to_the_profile() -> None:
    profile = load_profile("rpi4")
    metrics = SimulatedMetrics(profile, "x", real=lambda **_: dict(REAL_METRICS)).collect()
    assert metrics["memory_total_bytes"] == profile["inventory"]["memory_mb"] * 1024 * 1024
    assert metrics["memory_used_bytes"] == metrics["memory_total_bytes"] // 4
    assert metrics["disk_total_bytes"] == 32 * 1000**3
    assert metrics["cpu_percent"] == 50.0  # real load is kept


def test_temperature_follows_load_and_pi_throttles_when_hot() -> None:
    profile = load_profile("rpi5")
    load = {"cpu": 0.0}
    metrics = SimulatedMetrics(
        profile, "x", real=lambda **_: dict(REAL_METRICS, cpu_percent=load["cpu"]), rng=random.Random(1)
    )
    idle = metrics.collect()
    assert abs(idle["temperature_c"] - profile["thermal"]["idle_c"]) < 5
    assert idle["throttled"] == "0x0"

    metrics.load_c = 95.0  # a heat wave: full load runs past the throttle point
    load["cpu"] = 100.0
    for _ in range(10):
        hot = metrics.collect()
    assert hot["temperature_c"] > profile["thermal"]["throttle_c"]
    assert int(hot["throttled"], 16) & 0x4

    load["cpu"] = 0.0
    for _ in range(10):
        cooled = metrics.collect()
    flags = int(cooled["throttled"], 16)
    assert flags & 0xF == 0 and flags >> 16 & 0x4  # throttled since boot, not now


def test_jetson_reports_gpu_and_power_mode() -> None:
    metrics = SimulatedMetrics(load_profile("jetson-orin-nano"), "x", real=lambda **_: dict(REAL_METRICS)).collect()
    assert 0 <= metrics["gpu_percent"] <= 100
    assert metrics["power_mode"] == "15W"


def test_capability_overrides_win_over_probes() -> None:
    probes = dict.fromkeys(CAPABILITY_KEYS, True)
    caps = SimulatedCapabilities(load_profile("industrial-gateway"), real=lambda: dict(probes)).collect()
    assert caps["docker"] is False and caps["docker_compose"] is False
    assert caps["systemd"] is True


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def execv(self, path: str, argv: list[str]) -> None:
        self.calls.append(("execv", argv))

    def exit(self, code: int) -> None:
        self.calls.append(("exit", code))


def _actions(recorder: _Recorder, environ: dict[str, str] | None = None) -> SimulatedHostActions:
    return SimulatedHostActions(
        load_profile("rpi5"),
        environ=environ or {},
        execv=recorder.execv,
        exit_process=recorder.exit,
        argv=["/usr/local/bin/meteorcli", "run"],
        start_delay=0,
    )


def _wait_for(recorder: _Recorder) -> None:
    import time

    deadline = time.monotonic() + 2
    while not recorder.calls and time.monotonic() < deadline:
        time.sleep(0.01)


def test_simulated_reboot_restarts_the_agent_after_the_boot_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _actions(recorder).reboot()
    _wait_for(recorder)
    [(kind, argv)] = recorder.calls
    assert kind == "execv"
    assert argv[1:5] == ["-m", "edge_agent.platform.simulated", "--boot-delay", "20.0"]
    restarted = argv[argv.index("--") + 1 :]
    assert restarted == [sys.executable, "/usr/local/bin/meteorcli", "run"]

    # The boot step waits, then starts the same agent command again (and so reconnects).
    slept: list[float] = []
    started: list[list[str]] = []
    monkeypatch.setattr("edge_agent.platform.simulated.time.sleep", slept.append)
    monkeypatch.setattr("edge_agent.platform.simulated.os.execv", lambda path, args: started.append(args))
    _boot_main(argv[3:])
    assert slept == [20.0]
    assert started == [restarted]


def test_simulated_shutdown_stops_the_agent() -> None:
    recorder = _Recorder()
    _actions(recorder).shutdown()
    _wait_for(recorder)
    assert recorder.calls == [("exit", 0)]


def test_reboot_hook_replaces_the_process_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr("edge_agent.platform.simulated.subprocess.Popen", lambda argv, **_: started.append(list(argv)))
    recorder = _Recorder()
    _actions(recorder, {"METEORCLI_SIM_REBOOT_COMMAND": "virsh reboot sim-1"}).reboot()
    assert started == [["virsh", "reboot", "sim-1"]]
    assert recorder.calls == []


def test_user_changes_are_recorded_not_applied_by_default() -> None:
    actions = _actions(_Recorder())
    actions.add_user("ops", "secret", sudo=True)
    actions.set_password("ops", "other")
    assert [entry["action"] for entry in actions.recorded_users] == ["add_user", "set_password"]
    assert "secret" not in json.dumps(actions.recorded_users)


def test_selection_defaults_to_linux_and_needs_an_explicit_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge_agent.platform.linux import LinuxPlatform

    assert isinstance(selection.active_platform(), LinuxPlatform)

    selection.reset_active_platform()
    monkeypatch.setenv("METEORCLI_PLATFORM", "simulated")
    monkeypatch.setenv("METEORCLI_DEVICE_PROFILE", "nuc")
    monkeypatch.setenv("METEORCLI_SIM_ID", "sim-7")
    inventory = selection.collect_inventory()
    assert inventory["simulated"] is True
    assert inventory["hardware_model"] == "NUC13ANHi5"
    assert selection.collect_inventory()["serial_number"] == inventory["serial_number"]


def test_unknown_platform_falls_back_to_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METEORCLI_PLATFORM", "windows95")
    assert selection.platform_name() == "linux"


def test_action_sync_uses_the_simulated_host_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge_agent.actions import ActionSync

    monkeypatch.setenv("METEORCLI_PLATFORM", "simulated")
    sync = ActionSync(object(), "tok")  # type: ignore[arg-type]
    assert isinstance(sync._host, SimulatedHostActions)
