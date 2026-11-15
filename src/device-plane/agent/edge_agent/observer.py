"""What the agent watches between metrics: apps, network, Wi-Fi, its own errors, warnings.

Every reading is best effort: a missing tool or file leaves the key out.

- Apps (Docker containers): per-app numbers go into the metrics payload as flat
  ``app.<name>.<field>`` keys, so the data plane keeps their history like any
  other metric. Changes between two looks (started, exited, restarted, crash
  loop, health changed, removed) become events on ``devices/<id>/events``.
- Network: bytes per second received and sent since the last look.
- Wi-Fi: signal level in dBm from ``/proc/net/wireless``.
- Agent errors: ERROR log records of the agent since the last look.
- System warnings: journal entries at warning or worse, read through the
  root-owned ``meteorcli-logs`` helper and sent as ``log`` events, so they are
  kept and searchable in the cloud.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("edge_agent")

MAX_APPS = 20
# The data plane keeps metric keys of at most 64 characters: "app." + name + ".health_ms".
MAX_APP_NAME = 50
CRASH_LOOP_RESTARTS = 3
CRASH_LOOP_WINDOW_SECONDS = 600.0
MAX_LOG_EVENTS = 200
MAX_LOG_MESSAGE = 1000
_SKIP_INTERFACES = ("lo", "docker", "veth", "br-", "virbr", "wg")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

INSPECT_TEMPLATE = (
    "{{.Name}}|{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|"
    "{{.RestartCount}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}|"
    "{{range .State.Health.Log}}{{.Start.UnixNano}},{{.End.UnixNano}};{{end}}{{end}}"
)


def metric_name(name: str) -> str:
    """The app's name in metric keys: as is up to MAX_APP_NAME, else a prefix plus a hash.

    Two long names that share their first characters still get distinct keys.
    """
    if len(name) <= MAX_APP_NAME:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[: MAX_APP_NAME - 9]}~{digest}"


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=30, check=False)


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


# ------------------------------------------------------------------ network, Wi-Fi


def read_network_bytes(content: str | None = None) -> tuple[int, int] | None:
    """(received, sent) bytes over physical interfaces since boot."""
    content = _read_text("/proc/net/dev") if content is None else content
    if not content:
        return None
    rx = tx = 0
    found = False
    for line in content.splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        fields = rest.split()
        if not name or name.startswith(_SKIP_INTERFACES) or len(fields) < 9:
            continue
        try:
            rx += int(fields[0])
            tx += int(fields[8])
        except ValueError:
            continue
        found = True
    return (rx, tx) if found else None


def read_wifi_signal_dbm(content: str | None = None) -> float | None:
    """Signal level of the first wireless interface, in dBm."""
    content = _read_text("/proc/net/wireless") if content is None else content
    if not content:
        return None
    for line in content.splitlines()[2:]:
        _, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 3:
            continue
        try:
            level = float(fields[2].rstrip("."))
        except ValueError:
            continue
        # Some drivers report 0..100 quality-style levels instead of dBm; only dBm is useful here.
        if -120.0 <= level < 0:
            return level
    return None


class ErrorCounter(logging.Handler):
    """Counts the agent's ERROR records; ``take`` returns the count and starts again."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1

    def take(self) -> int:
        count, self.count = self.count, 0
        return count


# ------------------------------------------------------------------ apps


@dataclass
class AppState:
    name: str
    status: str
    running: bool
    exit_code: int | None
    oom_killed: bool
    restarts: int
    started_at: str
    health: str | None = None
    health_ms: float | None = None


def parse_inspect_line(line: str) -> AppState | None:
    parts = line.strip().split("|")
    if len(parts) < 7:
        return None
    name = parts[0].lstrip("/")
    if not name:
        return None
    try:
        exit_code: int | None = int(parts[3])
    except ValueError:
        exit_code = None
    try:
        restarts = int(parts[5])
    except ValueError:
        restarts = 0
    state = AppState(
        name=name,
        status=parts[1],
        running=parts[2].lower() == "true" and parts[1] != "restarting",
        exit_code=exit_code,
        oom_killed=parts[4].lower() == "true",
        restarts=restarts,
        started_at=parts[6],
    )
    if len(parts) >= 8 and parts[7]:
        state.health = parts[7]
        checks = [item for item in (parts[8] if len(parts) > 8 else "").split(";") if item]
        if checks:
            try:
                start, end = (int(value) for value in checks[-1].split(",", 1))
                if end > start:
                    state.health_ms = round((end - start) / 1e6, 1)
            except ValueError:
                pass
    return state


def _percent(value: str) -> float | None:
    try:
        return round(float(value.strip().rstrip("%")), 2)
    except ValueError:
        return None


_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}


def _bytes(value: str) -> float | None:
    text = value.strip().lower()
    for unit in sorted(_UNITS, key=len, reverse=True):
        if text.endswith(unit):
            try:
                return float(text[: -len(unit)]) * _UNITS[unit]
            except ValueError:
                return None
    return None


def parse_stats(output: str) -> dict[str, tuple[float | None, float | None]]:
    """``docker stats`` lines "name|12.5%|45.6MiB / 1.9GiB" -> {name: (cpu %, memory bytes)}."""
    stats: dict[str, tuple[float | None, float | None]] = {}
    for line in output.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 3:
            continue
        stats[parts[0].lstrip("/")] = (_percent(parts[1]), _bytes(parts[2].split("/")[0]))
    return stats


@dataclass
class AppObserver:
    """Looks at the Docker containers once per metrics interval and reports what changed."""

    runner: Runner = _default_runner
    clock: Callable[[], float] = time.monotonic
    _previous: dict[str, AppState] | None = None
    _restarts: dict[str, deque[float]] = field(default_factory=dict)
    _looping: set[str] = field(default_factory=set)

    def _states(self) -> dict[str, AppState] | None:
        try:
            listed = self.runner(["docker", "ps", "-a", "--format", "{{.Names}}"])
        except (OSError, subprocess.SubprocessError):
            return None
        if listed.returncode != 0:
            return None
        names = [name for name in (listed.stdout or "").split() if name][:MAX_APPS]
        if not names:
            return {}
        try:
            inspected = self.runner(["docker", "inspect", "-f", INSPECT_TEMPLATE, *names])
        except (OSError, subprocess.SubprocessError):
            return None
        # A container removed between ps and inspect also makes inspect exit 1, but
        # it still prints the others; only an answer with nothing in it is a failure
        # (keep the previous baseline instead of reporting every app removed).
        if inspected.returncode != 0 and not (inspected.stdout or "").strip():
            return None
        states = {}
        for line in (inspected.stdout or "").splitlines():
            state = parse_inspect_line(line)
            if state is not None:
                states[state.name] = state
        return states

    def _stats(self) -> dict[str, tuple[float | None, float | None]]:
        try:
            result = self.runner(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"])
        except (OSError, subprocess.SubprocessError):
            return {}
        return parse_stats(result.stdout or "") if result.returncode == 0 else {}

    def observe(self) -> tuple[dict[str, float], list[dict[str, Any]]]:
        """(metric keys, lifecycle events). The first look sets the baseline and reports no events."""
        states = self._states()
        if states is None:
            return {}, []
        stats = self._stats() if any(state.running for state in states.values()) else {}
        metrics: dict[str, float] = {}
        for name, state in states.items():
            prefix = f"app.{metric_name(name)}."
            metrics[prefix + "up"] = 1 if state.running else 0
            metrics[prefix + "restarts"] = state.restarts
            if state.health in ("healthy", "unhealthy"):
                metrics[prefix + "healthy"] = 1 if state.health == "healthy" else 0
            if state.health_ms is not None:
                metrics[prefix + "health_ms"] = state.health_ms
            cpu, memory = stats.get(name, (None, None))
            if cpu is not None:
                metrics[prefix + "cpu"] = cpu
            if memory is not None:
                metrics[prefix + "mem"] = round(memory)
        events = [] if self._previous is None else self._changes(self._previous, states)
        self._previous = states
        return metrics, events

    def _changes(self, before: dict[str, AppState], now: dict[str, AppState]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        at = self.clock()
        for name, state in now.items():
            old = before.get(name)
            if old is None:
                if state.running:
                    events.append({"type": "app.started", "app": name})
                continue
            if old.running and not state.running:
                events.append(
                    {
                        "type": "app.exited",
                        "app": name,
                        "exit_code": state.exit_code,
                        "oom_killed": state.oom_killed,
                        "detail": state.status,
                    }
                )
            elif state.running and (not old.running or state.started_at != old.started_at):
                if state.restarts > old.restarts:
                    events.append({"type": "app.restarted", "app": name, "restarts": state.restarts})
                else:
                    events.append({"type": "app.started", "app": name})
            elif state.restarts > old.restarts:
                events.append({"type": "app.restarted", "app": name, "restarts": state.restarts})
            if state.oom_killed and not old.oom_killed:
                events.append({"type": "app.oom_killed", "app": name, "exit_code": state.exit_code})
            if state.health != old.health and state.health in ("healthy", "unhealthy"):
                events.append({"type": f"app.{state.health}", "app": name, "health_ms": state.health_ms})
            # Crash loop: several restarts inside the window; reported once until it calms down.
            history = self._restarts.setdefault(name, deque())
            history.extend([at] * max(0, min(state.restarts - old.restarts, 10)))
            while history and at - history[0] > CRASH_LOOP_WINDOW_SECONDS:
                history.popleft()
            if len(history) >= CRASH_LOOP_RESTARTS or (state.status == "restarting" and len(history) > 1):
                if name not in self._looping:
                    self._looping.add(name)
                    events.append({"type": "app.crash_loop", "app": name, "restarts": state.restarts})
            elif not history:
                self._looping.discard(name)
        for name in before.keys() - now.keys():
            events.append({"type": "app.removed", "app": name})
            self._restarts.pop(name, None)
            self._looping.discard(name)
        return events


# ------------------------------------------------------------------ system warnings


class WarningForwarder:
    """Journal warnings since the last cursor, as ``log`` events (bounded per look)."""

    def __init__(self, *, runner: Runner = _default_runner, helper: Callable[..., list[str]] | None = None) -> None:
        from edge_agent import logs

        self.runner = runner
        self.helper = helper or logs.helper_command
        self.available = logs.helper_installed
        self.cursor: str | None = None

    def collect(self) -> list[dict[str, Any]]:
        if not self.available():
            return []
        args = ["warnings", *(["--after-cursor", self.cursor] if self.cursor else [])]
        try:
            result = self.runner(self.helper(*args))
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        events: list[dict[str, Any]] = []
        for line in (result.stdout or "").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("message"), str):
                continue
            if isinstance(entry.get("cursor"), str):
                self.cursor = entry["cursor"]
            event: dict[str, Any] = {
                "type": "log",
                "message": entry["message"][:MAX_LOG_MESSAGE],
                "priority": entry.get("priority"),
                "unit": entry.get("unit"),
                "source": entry.get("source"),
            }
            if isinstance(entry.get("timestamp"), str):
                event["timestamp"] = entry["timestamp"]
            events.append(event)
        return events[-MAX_LOG_EVENTS:]


# ------------------------------------------------------------------ all together


class DeviceObserver:
    """Extra metrics and events for one metrics interval."""

    def __init__(
        self,
        *,
        apps: AppObserver | None = None,
        warnings: WarningForwarder | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.apps = apps or AppObserver()
        self.warnings = warnings or WarningForwarder()
        self.errors = ErrorCounter()
        self.clock = clock
        self._network: tuple[float, int, int] | None = None

    def install(self) -> None:
        agent_logger = logging.getLogger("edge_agent")
        if self.errors not in agent_logger.handlers:
            agent_logger.addHandler(self.errors)

    def network_rates(self, reading: tuple[int, int] | None) -> dict[str, float]:
        if reading is None:
            return {}
        now = self.clock()
        previous, self._network = self._network, (now, *reading)
        if previous is None:
            return {}
        elapsed = now - previous[0]
        rx, tx = reading[0] - previous[1], reading[1] - previous[2]
        if elapsed <= 0 or rx < 0 or tx < 0:  # counters reset (reboot, interface gone)
            return {}
        return {"net_rx_bps": round(rx / elapsed, 1), "net_tx_bps": round(tx / elapsed, 1)}

    def observe(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        metrics: dict[str, Any] = {}
        events: list[dict[str, Any]] = []
        for name, step in (
            ("network", lambda: metrics.update(self.network_rates(read_network_bytes()))),
            ("wifi", lambda: metrics.update({"wifi_signal_dbm": read_wifi_signal_dbm()})),
            ("apps", lambda: self._apps(metrics, events)),
            ("warnings", lambda: events.extend(self.warnings.collect())),
        ):
            try:
                step()
            except Exception:  # one broken reading must not stop the others
                logger.debug("Observer step %s failed", name, exc_info=True)
        if metrics.get("wifi_signal_dbm") is None:
            metrics.pop("wifi_signal_dbm", None)
        metrics["agent_errors"] = self.errors.take()
        return metrics, events

    def _apps(self, metrics: dict[str, Any], events: list[dict[str, Any]]) -> None:
        app_metrics, app_events = self.apps.observe()
        metrics.update(app_metrics)
        events.extend(app_events)
