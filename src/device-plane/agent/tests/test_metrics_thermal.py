"""Thermal zone selection across board families."""

from __future__ import annotations

from pathlib import Path

from edge_agent import metrics


def test_prefers_cpu_thermal_zone(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "thermal"
    z0 = root / "thermal_zone0"
    z1 = root / "thermal_zone1"
    z0.mkdir(parents=True)
    z1.mkdir(parents=True)
    (z0 / "type").write_text("gpu-thermal\n", encoding="utf-8")
    (z0 / "temp").write_text("99000\n", encoding="utf-8")
    (z1 / "type").write_text("cpu-thermal\n", encoding="utf-8")
    (z1 / "temp").write_text("45123\n", encoding="utf-8")

    monkeypatch.setattr(metrics, "_THERMAL_ROOT", str(root))
    assert metrics.read_temperature_c() == 45.1


def test_snapshot_carries_percentages_bytes_and_load(monkeypatch) -> None:
    meminfo = "MemTotal:        1000 kB\nMemFree:          100 kB\nMemAvailable:     250 kB\n"
    monkeypatch.setattr(metrics, "_read_text", lambda path: meminfo if path == "/proc/meminfo" else None)
    monkeypatch.setattr(metrics, "read_disk_bytes", lambda path="/": (300, 1200))
    monkeypatch.setattr(metrics.os, "getloadavg", lambda: (0.514, 0.25, 0.1))
    snapshot = metrics.collect_metrics(sample_cpu=False)
    assert snapshot["memory_percent"] == 75.0
    assert snapshot["memory_used_bytes"] == 750 * 1024 and snapshot["memory_total_bytes"] == 1000 * 1024
    assert snapshot["disk_percent"] == 25.0
    assert snapshot["disk_used_bytes"] == 300 and snapshot["disk_total_bytes"] == 1200
    assert (snapshot["load_1m"], snapshot["load_5m"], snapshot["load_15m"]) == (0.51, 0.25, 0.1)
    assert "uptime_seconds" not in snapshot  # /proc/uptime unreadable here
    assert "cpu_percent" not in snapshot
