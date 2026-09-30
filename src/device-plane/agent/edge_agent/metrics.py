"""Best-effort local health metrics. Missing readings are omitted, never fatal."""

from __future__ import annotations

import os
import time
from typing import Any

_CPU_SAMPLE_SECONDS = 0.1


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _cpu_times() -> tuple[int, int] | None:
    content = _read_text("/proc/stat")
    if not content:
        return None
    for line in content.splitlines():
        if not line.startswith("cpu "):
            continue
        parts = line.split()
        if len(parts) < 5:
            return None
        values = [int(part) for part in parts[1:] if part.isdigit()]
        if len(values) < 4:
            return None
        total = sum(values)
        idle = values[3]
        if len(values) > 4:
            idle += values[4]  # iowait
        return total, idle
    return None


def read_cpu_percent() -> float | None:
    first = _cpu_times()
    if first is None:
        return None
    time.sleep(_CPU_SAMPLE_SECONDS)
    second = _cpu_times()
    if second is None:
        return None
    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    busy = 1.0 - (idle_delta / total_delta)
    return round(max(0.0, min(100.0, busy * 100.0)), 1)


def read_memory_percent() -> float | None:
    content = _read_text("/proc/meminfo")
    if not content:
        return None
    total = None
    available = None
    for line in content.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                total = int(parts[1])
        elif line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                available = int(parts[1])
        if total is not None and available is not None:
            break
    if not total or available is None:
        return None
    used = max(0, total - available)
    return round(min(100.0, used * 100.0 / total), 1)


def read_disk_percent(path: str = "/") -> float | None:
    try:
        stats = os.statvfs(path)
    except OSError:
        return None
    total = stats.f_blocks * stats.f_frsize
    if total <= 0:
        return None
    free = stats.f_bavail * stats.f_frsize
    used = max(0, total - free)
    return round(min(100.0, used * 100.0 / total), 1)


def read_temperature_c() -> float | None:
    raw = _read_text("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return None
    try:
        milli = int(raw.strip())
    except ValueError:
        return None
    return round(milli / 1000.0, 1)


def read_uptime_seconds() -> int | None:
    raw = _read_text("/proc/uptime")
    if raw is None:
        return None
    try:
        return int(float(raw.split()[0]))
    except (ValueError, IndexError):
        return None


def collect_metrics(*, sample_cpu: bool = True) -> dict[str, Any]:
    """Return a metrics dict; never raises for missing system files."""
    snapshot: dict[str, Any] = {}
    if sample_cpu:
        cpu = read_cpu_percent()
        if cpu is not None:
            snapshot["cpu_percent"] = cpu
    memory = read_memory_percent()
    if memory is not None:
        snapshot["memory_percent"] = memory
    disk = read_disk_percent()
    if disk is not None:
        snapshot["disk_percent"] = disk
    snapshot["temperature_c"] = read_temperature_c()
    return snapshot
