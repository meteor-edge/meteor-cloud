"""Simulated device platform: a Raspberry Pi, NUC or Jetson without the hardware.

The real agent keeps running; only the hardware facts come from a device profile
(``profiles/<name>.json``). Hostname, IP address and OS fields stay real, and so do
CPU, memory and disk load. The board model, CPU, core count, memory size and
architecture come from the profile, temperature follows load with some noise, and each
instance gets a stable serial number and MAC address of its own.

A simulated device always reports ``simulated: true`` in its inventory, so the console
can mark it and nobody mistakes it for a real board. It is only used when
``METEORCLI_PLATFORM=simulated`` is set; see docs/fleet/simulator.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from edge_agent import capabilities as capabilities_mod
from edge_agent import inventory as inventory_mod
from edge_agent import metrics as metrics_mod
from edge_agent.platform.ports import HostActionError

logger = logging.getLogger("edge_agent")

ENV_PROFILE = "METEORCLI_DEVICE_PROFILE"
ENV_SIM_ID = "METEORCLI_SIM_ID"
ENV_REBOOT_COMMAND = "METEORCLI_SIM_REBOOT_COMMAND"
ENV_SHUTDOWN_COMMAND = "METEORCLI_SIM_SHUTDOWN_COMMAND"
ENV_USERS = "METEORCLI_SIM_USERS"
DEFAULT_PROFILE = "rpi5"
_INVENTORY_FIELDS = ("hardware_model", "cpu_model", "cpu_cores", "memory_mb", "architecture")
# Raspberry Pi ``vcgencmd get_throttled`` bits: now (low word) and since boot (high word).
_SOFT_TEMP_LIMIT = 0x8
_THROTTLED = 0x4
_FREQ_CAPPED = 0x2


class ProfileError(ValueError):
    """The device profile is missing or malformed."""


def available_profiles() -> list[str]:
    """Names of the profiles that ship with the agent."""
    folder = resources.files("edge_agent.platform") / "profiles"
    return sorted(entry.name.removesuffix(".json") for entry in folder.iterdir() if entry.name.endswith(".json"))


def load_profile(name_or_path: str) -> dict[str, Any]:
    """A shipped profile by name (``rpi5``) or a profile file by path (``./my-board.json``)."""
    value = name_or_path.strip()
    try:
        if value.endswith(".json") or os.sep in value:
            text = Path(value).read_text(encoding="utf-8")
        else:
            if not value.replace("-", "").replace("_", "").isalnum():
                raise ProfileError(f"Not a profile name: {value!r}.")
            text = (resources.files("edge_agent.platform") / "profiles" / f"{value}.json").read_text(encoding="utf-8")
    except OSError as exc:
        known = ", ".join(available_profiles())
        raise ProfileError(f"Unknown device profile {value!r} (shipped profiles: {known}).") from exc
    try:
        profile = json.loads(text)
    except ValueError as exc:
        raise ProfileError(f"Device profile {value!r} is not valid JSON.") from exc
    if not isinstance(profile, dict) or not isinstance(profile.get("inventory"), dict):
        raise ProfileError(f"Device profile {value!r} needs an 'inventory' object.")
    missing = [field for field in _INVENTORY_FIELDS if profile["inventory"].get(field) in (None, "")]
    if missing:
        raise ProfileError(f"Device profile {value!r} is missing inventory fields: {', '.join(missing)}.")
    profile.setdefault("name", Path(value).stem)
    return profile


def instance_seed(explicit: str | None = None) -> str:
    """What makes this simulated device distinct: ``METEORCLI_SIM_ID``, else the hostname.

    A container's hostname is its id, so one container keeps its identity across restarts
    and two containers get different ones. Set ``METEORCLI_SIM_ID`` for a fleet.
    """
    value = (explicit if explicit is not None else os.environ.get(ENV_SIM_ID, "")).strip()
    if value:
        return value
    try:
        return os.uname().nodename or "meteorcli-sim"
    except AttributeError:  # pragma: no cover - non-POSIX
        return "meteorcli-sim"


def simulated_identity(profile_name: str, seed: str) -> tuple[str, str]:
    """A stable (serial number, MAC address) for one simulated device.

    The MAC is locally administered (``02:…``), so it never collides with a real
    vendor's address, and the serial starts with ``SIM-``.
    """
    digest = hashlib.sha256(f"{profile_name}\n{seed}".encode()).digest()
    serial = f"SIM-{digest[:6].hex().upper()}"
    mac = ":".join(f"{byte:02x}" for byte in (0x02, *digest[6:11]))
    return serial, mac


class SimulatedInventory:
    def __init__(self, profile: dict[str, Any], seed: str, *, real: Callable[[], dict[str, Any]] | None = None) -> None:
        self.profile = profile
        self.serial, self.mac = simulated_identity(str(profile["name"]), seed)
        self._real = real or inventory_mod.collect_inventory

    def collect(self) -> dict[str, Any]:
        snapshot = dict(self._real())
        snapshot.update({field: self.profile["inventory"][field] for field in _INVENTORY_FIELDS})
        snapshot["serial_number"] = self.serial
        snapshot["mac_addresses"] = [self.mac]
        snapshot["simulated"] = True
        snapshot["device_profile"] = self.profile["name"]
        return snapshot


class SimulatedMetrics:
    """Real load from the host, sized to the profile, with a simulated temperature."""

    def __init__(
        self,
        profile: dict[str, Any],
        seed: str,
        *,
        real: Callable[..., dict[str, Any]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.profile = profile
        self._real = real or metrics_mod.collect_metrics
        self._rng = rng or random.Random(seed)
        thermal = profile.get("thermal") or {}
        self.idle_c = float(thermal.get("idle_c", 45.0))
        self.load_c = float(thermal.get("load_c", 75.0))
        self.throttle_c = float(thermal.get("throttle_c", 85.0))
        self.noise_c = float(thermal.get("noise_c", 1.0))
        self._temperature: float | None = None
        self._throttled_since_boot = 0
        self._lock = threading.Lock()

    def _next_temperature(self, cpu_percent: float | None) -> float:
        load = max(0.0, min(100.0, cpu_percent if cpu_percent is not None else 0.0)) / 100.0
        target = self.idle_c + (self.load_c - self.idle_c) * load
        # Boards heat and cool over a few readings, not instantly.
        previous = self._temperature if self._temperature is not None else target
        value = previous + (target - previous) * 0.5 + self._rng.gauss(0.0, self.noise_c)
        self._temperature = value
        return round(value, 1)

    def collect(self, *, sample_cpu: bool = True) -> dict[str, Any]:
        snapshot = dict(self._real(sample_cpu=sample_cpu))
        memory_total = int(self.profile["inventory"]["memory_mb"]) * 1024 * 1024
        if snapshot.get("memory_percent") is not None:
            snapshot["memory_total_bytes"] = memory_total
            snapshot["memory_used_bytes"] = int(memory_total * float(snapshot["memory_percent"]) / 100)
        disk_gb = self.profile.get("disk_gb")
        if disk_gb and snapshot.get("disk_percent") is not None:
            disk_total = int(float(disk_gb) * 1000**3)
            snapshot["disk_total_bytes"] = disk_total
            snapshot["disk_used_bytes"] = int(disk_total * float(snapshot["disk_percent"]) / 100)
        with self._lock:
            temperature = self._next_temperature(snapshot.get("cpu_percent"))
            snapshot["temperature_c"] = temperature
            extras = self.profile.get("extras")
            if extras == "raspberry_pi":
                snapshot["throttled"] = self._pi_throttled(temperature)
            elif extras == "jetson":
                snapshot.update(self._jetson(snapshot.get("cpu_percent"), temperature))
        return snapshot

    def _pi_throttled(self, temperature: float) -> str:
        now = 0
        if temperature >= self.throttle_c - 5:
            now |= _SOFT_TEMP_LIMIT
        if temperature >= self.throttle_c:
            now |= _THROTTLED | _FREQ_CAPPED
        self._throttled_since_boot |= now
        return hex(now | (self._throttled_since_boot << 16))

    def _jetson(self, cpu_percent: float | None, temperature: float) -> dict[str, Any]:
        base = cpu_percent if cpu_percent is not None else 0.0
        gpu = max(0.0, min(100.0, base * 0.8 + self._rng.gauss(0.0, 3.0)))
        jetson = self.profile.get("jetson") or {}
        return {
            "gpu_percent": round(gpu, 1),
            "gpu_temperature_c": round(temperature - 1.5, 1),
            "power_mode": str(jetson.get("power_mode", "15W")),
        }


class SimulatedCapabilities:
    """Real probes, with the profile's overrides on top (e.g. ``{"docker": false}``)."""

    def __init__(self, profile: dict[str, Any], *, real: Callable[[], dict[str, Any]] | None = None) -> None:
        self.overrides = {str(k): v for k, v in (profile.get("capabilities") or {}).items()}
        self._real = real or capabilities_mod.collect_capabilities

    def collect(self) -> dict[str, Any]:
        return {**self._real(), **self.overrides}


def _boot_argv(delay: float, argv: Sequence[str]) -> list[str]:
    return [sys.executable, "-m", "edge_agent.platform.simulated", "--boot-delay", str(delay), "--", *argv]


def _run_hook(command: str) -> None:
    try:
        subprocess.Popen(shlex.split(command), start_new_session=True)  # noqa: S603 — operator-configured hook
    except (OSError, ValueError) as exc:
        raise HostActionError(f"The simulator hook could not run ({exc}).") from exc


class SimulatedHostActions:
    """Restart and shut-down act on the agent process; user changes are recorded only.

    ``reboot`` replaces the agent process with one that waits the profile's boot delay
    and then starts the same command again, so the device goes offline and reconnects
    like a real board. With ``METEORCLI_SIM_REBOOT_COMMAND`` set, that command runs
    instead (e.g. to restart a VM). ``shutdown`` ends the agent process, or runs
    ``METEORCLI_SIM_SHUTDOWN_COMMAND``. User changes go to the real host only with
    ``METEORCLI_SIM_USERS=host``.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        *,
        environ: dict[str, str] | None = None,
        execv: Callable[[str, list[str]], Any] = os.execv,
        exit_process: Callable[[int], Any] = os._exit,
        argv: Sequence[str] | None = None,
        start_delay: float = 1.0,
        host_users: Any = None,
    ) -> None:
        self.boot_delay = float(profile.get("boot_delay_seconds", 20))
        self._env = environ if environ is not None else os.environ
        self._execv = execv
        self._exit = exit_process
        self._argv = list(argv) if argv is not None else [*sys.argv]
        self._start_delay = start_delay
        self._host_users = host_users
        self.recorded_users: list[dict[str, Any]] = []

    def _later(self, action: Callable[[], None]) -> None:
        # Give the action report a moment to leave before the process goes away.
        def run() -> None:
            time.sleep(self._start_delay)
            action()

        threading.Thread(target=run, name="meteorcli-sim-power", daemon=True).start()

    def reboot(self) -> None:
        hook = self._env.get(ENV_REBOOT_COMMAND, "").strip()
        if hook:
            _run_hook(hook)
            return
        argv = _boot_argv(self.boot_delay, [sys.executable, *self._argv])
        logger.warning("Simulated restart: the agent goes offline for %.0fs, then starts again.", self.boot_delay)
        self._later(lambda: self._execv(sys.executable, argv))

    def shutdown(self) -> None:
        hook = self._env.get(ENV_SHUTDOWN_COMMAND, "").strip()
        if hook:
            _run_hook(hook)
            return
        logger.warning("Simulated shut-down: the agent stops.")
        self._later(lambda: self._exit(0))

    def _users(self) -> Any:
        if self._env.get(ENV_USERS, "record").strip().lower() != "host":
            return None
        if self._host_users is None:
            from edge_agent.platform.linux import SystemdHostActions

            self._host_users = SystemdHostActions()
        return self._host_users

    def set_password(self, username: str, password: str) -> None:
        host = self._users()
        if host is not None:
            host.set_password(username, password)
            return
        self.recorded_users.append({"action": "set_password", "username": username})
        logger.info("Simulated device: recorded a password change for %s (host unchanged).", username)

    def add_user(
        self,
        username: str,
        password: str,
        *,
        sudo: bool = False,
        authorized_key: str | None = None,
    ) -> None:
        host = self._users()
        if host is not None:
            host.add_user(username, password, sudo=sudo, authorized_key=authorized_key)
            return
        self.recorded_users.append({"action": "add_user", "username": username, "sudo": sudo})
        logger.info("Simulated device: recorded new user %s (host unchanged).", username)


class SimulatedPlatform:
    """All four ports for one simulated board."""

    def __init__(self, profile: str | dict[str, Any] | None = None, *, seed: str | None = None) -> None:
        if profile is None:
            profile = os.environ.get(ENV_PROFILE, "").strip() or DEFAULT_PROFILE
        self.profile = load_profile(profile) if isinstance(profile, str) else profile
        self.seed = instance_seed(seed)
        self._inventory = SimulatedInventory(self.profile, self.seed)
        self._metrics = SimulatedMetrics(self.profile, self.seed)
        self._capabilities = SimulatedCapabilities(self.profile)
        self._host_actions = SimulatedHostActions(self.profile)

    @property
    def inventory(self) -> SimulatedInventory:
        return self._inventory

    @property
    def metrics(self) -> SimulatedMetrics:
        return self._metrics

    @property
    def capabilities(self) -> SimulatedCapabilities:
        return self._capabilities

    @property
    def host_actions(self) -> SimulatedHostActions:
        return self._host_actions


def _boot_main(args: Sequence[str]) -> int:
    """``python -m edge_agent.platform.simulated --boot-delay N -- <argv…>``: wait, then exec."""
    if len(args) < 4 or args[0] != "--boot-delay" or args[2] != "--":
        print("usage: python -m edge_agent.platform.simulated --boot-delay SECONDS -- COMMAND...", file=sys.stderr)
        return 2
    time.sleep(max(0.0, float(args[1])))
    command = list(args[3:])
    os.execv(command[0], command)
    return 0  # pragma: no cover - execv does not return


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_boot_main(sys.argv[1:]))
