"""Tests for best-effort inventory collection."""

from __future__ import annotations

from pathlib import Path

from edge_agent import inventory


def _net_dir(tmp_path: Path, ifaces: dict[str, str]) -> Path:
    net = tmp_path / "net"
    net.mkdir()
    for name, mac in ifaces.items():
        iface = net / name
        iface.mkdir()
        (iface / "address").write_text(mac + "\n")
    return net


def _patch_net(monkeypatch, net: Path, route: str | None) -> None:
    monkeypatch.setattr(inventory, "_sys_class_net", lambda: net)

    def read_text(path: str) -> str | None:
        if path == "/proc/net/route":
            return route
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None

    monkeypatch.setattr(inventory, "_read_text", read_text)


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


def test_primary_mac_stays_wired_when_the_default_route_is_wifi(monkeypatch, tmp_path) -> None:
    net = _net_dir(
        tmp_path,
        {
            "eth0": "aa:bb:cc:dd:ee:01",
            "wlan0": "aa:bb:cc:dd:ee:02",
            "docker0": "aa:bb:cc:dd:ee:03",
        },
    )
    _patch_net(
        monkeypatch,
        net,
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "wlan0 00000000 0101A8C0 0003 0 0 100 00000000 0 0 0\n",
    )
    assert inventory.read_mac_addresses() == ["aa:bb:cc:dd:ee:01"]


def test_primary_mac_skips_virtual_prefers_eth(monkeypatch, tmp_path) -> None:
    net = _net_dir(
        tmp_path,
        {
            "docker0": "aa:bb:cc:dd:ee:03",
            "wlan0": "aa:bb:cc:dd:ee:02",
            "eth0": "aa:bb:cc:dd:ee:01",
        },
    )
    _patch_net(monkeypatch, net, None)
    assert inventory.read_mac_addresses() == ["aa:bb:cc:dd:ee:01"]


def test_primary_mac_falls_back_to_wifi(monkeypatch, tmp_path) -> None:
    net = _net_dir(tmp_path, {"wlan0": "aa:bb:cc:dd:ee:02", "docker0": "aa:bb:cc:dd:ee:03"})
    _patch_net(monkeypatch, net, None)
    assert inventory.read_mac_addresses() == ["aa:bb:cc:dd:ee:02"]


def test_primary_mac_ignores_virtual_interfaces_only_system(monkeypatch, tmp_path) -> None:
    net = _net_dir(tmp_path, {"docker0": "aa:bb:cc:dd:ee:03"})
    _patch_net(monkeypatch, net, None)
    assert inventory.read_mac_addresses() == []


def _files(monkeypatch, files: dict[str, str]) -> None:
    monkeypatch.setattr(inventory, "_read_text", lambda path: files.get(path))


def test_raspberry_pi_reports_model_and_serial_from_the_device_tree(monkeypatch) -> None:
    _files(
        monkeypatch,
        {
            "/sys/firmware/devicetree/base/model": "Raspberry Pi 5 Model B Rev 1.0\x00",
            "/sys/firmware/devicetree/base/serial-number": "10000000abcd1234\x00",
        },
    )
    assert inventory.read_hardware_model() == "Raspberry Pi 5 Model B Rev 1.0"
    assert inventory.read_serial_number() == "10000000abcd1234"


def test_pc_reports_dmi_model_and_skips_placeholders(monkeypatch) -> None:
    _files(
        monkeypatch,
        {
            "/sys/class/dmi/id/product_name": "System Product Name",
            "/sys/class/dmi/id/board_name": "NUC13ANHi5",
            "/sys/class/dmi/id/product_serial": "To Be Filled By O.E.M.",
            "/sys/class/dmi/id/board_serial": "0000000000",
        },
    )
    assert inventory.read_hardware_model() == "NUC13ANHi5"
    assert inventory.read_serial_number() is None
