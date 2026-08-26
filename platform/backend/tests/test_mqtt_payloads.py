"""Unit tests for MQTT status and metrics payload validation."""

from __future__ import annotations

from app.modules.mqtt.payloads import parse_metrics_payload, parse_status_payload


def test_parse_status_accepts_online_offline() -> None:
    assert parse_status_payload('{"status":"online","agent_version":"0.2.0"}') == "online"
    assert parse_status_payload('{"status":"offline"}') == "offline"


def test_parse_status_rejects_garbage() -> None:
    assert parse_status_payload("not-json") is None
    assert parse_status_payload('{"status":"degraded"}') is None
    assert parse_status_payload("[]") is None


def test_parse_metrics_keeps_valid_snapshot() -> None:
    snapshot = parse_metrics_payload(
        '{"timestamp":"2026-08-25T12:00:00Z","cpu_percent":18.4,'
        '"memory_percent":42.1,"disk_percent":61.3,"temperature_c":54.2}'
    )
    assert snapshot == {
        "timestamp": "2026-08-25T12:00:00Z",
        "cpu_percent": 18.4,
        "memory_percent": 42.1,
        "disk_percent": 61.3,
        "temperature_c": 54.2,
    }


def test_parse_metrics_drops_out_of_range_and_allows_null_temp() -> None:
    snapshot = parse_metrics_payload(
        '{"cpu_percent":180,"memory_percent":10,"temperature_c":null}'
    )
    assert snapshot is not None
    assert "cpu_percent" not in snapshot
    assert snapshot["memory_percent"] == 10.0
    assert snapshot["temperature_c"] is None


def test_parse_metrics_rejects_empty() -> None:
    assert parse_metrics_payload("{}") is None
    assert parse_metrics_payload("[]") is None
