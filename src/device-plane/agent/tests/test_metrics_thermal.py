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
