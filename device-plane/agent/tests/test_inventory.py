"""Tests for best-effort inventory collection."""

from __future__ import annotations

from edge_agent import inventory


def test_collect_inventory_has_all_keys() -> None:
    data = inventory.collect_inventory()
    expected = {
        "serial_number",
        "mac_addresses",
        "hostname",
        "os_name",
        "os_version",
        "kernel_version",
        "architecture",
        "cpu_model",
        "cpu_cores",
        "memory_mb",
    }
    assert expected.issubset(data.keys())
    assert isinstance(data["mac_addresses"], list)


def test_read_os_release_parses_fields(monkeypatch) -> None:
    sample = 'NAME="Ubuntu"\nVERSION_ID="22.04"\nID=ubuntu\n'
    monkeypatch.setattr(inventory, "_read_text", lambda path: sample)
    name, version = inventory.read_os_release()
    assert name == "Ubuntu"
    assert version == "22.04"


def test_read_os_release_missing(monkeypatch) -> None:
    monkeypatch.setattr(inventory, "_read_text", lambda path: None)
    assert inventory.read_os_release() == (None, None)


def test_read_memory_mb(monkeypatch) -> None:
    monkeypatch.setattr(
        inventory, "_read_text", lambda path: "MemTotal:  2048000 kB\nMemFree: 1000 kB\n"
    )
    assert inventory.read_memory_mb() == 2000


def test_collect_inventory_tolerates_missing_system(monkeypatch) -> None:
    # Simulate a system where nothing is readable.
    monkeypatch.setattr(inventory, "_read_text", lambda path: None)
    monkeypatch.setattr(inventory, "read_mac_addresses", lambda: [])
    data = inventory.collect_inventory()
    assert data["os_name"] is None
    assert data["mac_addresses"] == []
