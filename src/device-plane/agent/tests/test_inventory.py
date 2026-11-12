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
        "local_ip",
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


def test_arm_cpuinfo_uses_hardware_when_model_name_missing(monkeypatch) -> None:
    sample = (
        "processor\t: 0\n"
        "model name\t: \n"
        "Hardware\t: BCM2712\n"
        "Revision\t: d04170\n"
        "Serial\t\t: 10000000abcd1234\n"
        "Model\t\t: Raspberry Pi 5 Model B Rev 1.0\n"
        "processor\t: 1\n"
        "processor\t: 2\n"
        "processor\t: 3\n"
    )
    monkeypatch.setattr(inventory, "_read_text", lambda path: sample if path == "/proc/cpuinfo" else None)
    model, cores = inventory.read_cpu()
    assert model == "Raspberry Pi 5 Model B Rev 1.0"
    assert cores == 4


def test_aarch64_cpu_part_is_reported_as_a_cpu_name(monkeypatch) -> None:
    sample = (
        "processor\t: 0\n"
        "CPU implementer\t: 0x41\n"
        "CPU part\t: 0xd0b\n"
        "CPU revision\t: 1\n"
        "processor\t: 1\n"
        "processor\t: 2\n"
        "processor\t: 3\n"
    )
    monkeypatch.setattr(inventory, "_read_text", lambda path: sample if path == "/proc/cpuinfo" else None)
    model, cores = inventory.read_cpu()
    assert model == "ARM Cortex-A76"
    assert cores == 4


def test_serial_comes_from_cpuinfo_when_the_device_tree_is_missing(monkeypatch) -> None:
    sample = "processor\t: 0\nSerial\t\t: 10000000abcd1234\n"
    monkeypatch.setattr(inventory, "_read_text", lambda path: sample if path == "/proc/cpuinfo" else None)
    assert inventory.read_serial_number() == "10000000abcd1234"


def test_local_ip_prefers_wired_and_skips_link_local(monkeypatch, tmp_path) -> None:
    net = _net_dir(
        tmp_path,
        {"docker0": "aa:bb:cc:dd:ee:03", "wlan0": "aa:bb:cc:dd:ee:02", "eth0": "aa:bb:cc:dd:ee:01"},
    )
    monkeypatch.setattr(inventory, "_sys_class_net", lambda: net)
    addresses = {"eth0": "169.254.8.1", "wlan0": "192.168.1.30", "docker0": "172.17.0.1"}
    monkeypatch.setattr(inventory, "_iface_ipv4", lambda name: addresses.get(name))
    assert inventory.read_local_ipv4() == "192.168.1.30"

    addresses["eth0"] = "192.168.1.20"
    assert inventory.read_local_ipv4() == "192.168.1.20"


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


ROUTE_HEADER = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"


def _route(*rows: tuple[str, int]) -> str:
    """Default routes: (interface, metric)."""
    lines = [f"{iface}\t00000000\t0100A8C0\t0003\t0\t0\t{metric}\t00000000\t0\t0\t0" for iface, metric in rows]
    return ROUTE_HEADER + "\n".join([*lines, "eth0\t0000A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0"])


def _links(tmp_path: Path) -> Path:
    net = _net_dir(tmp_path, {"eth0": "aa:aa:aa:aa:aa:01", "wlan0": "aa:aa:aa:aa:aa:02", "wwan0": "aa:aa:aa:aa:aa:03"})
    for iface in ("eth0", "wlan0", "wwan0"):
        (net / iface / "type").write_text("1\n")
        (net / iface / "device").mkdir()
    (net / "wlan0" / "uevent").write_text("DEVTYPE=wlan\nINTERFACE=wlan0\n")
    (net / "wwan0" / "uevent").write_text("DEVTYPE=wwan\n")
    (net / "wg0").mkdir()
    return net


def test_uplink_is_the_default_route_with_the_lowest_metric(monkeypatch, tmp_path) -> None:
    net = _links(tmp_path)
    # NetworkManager: Ethernet 100, Wi-Fi 600. With the cable in, traffic uses Ethernet.
    _patch_net(monkeypatch, net, _route(("wlan0", 600), ("eth0", 100)))
    assert inventory.read_uplink() == {"uplink_type": "ethernet", "uplink_interface": "eth0"}
    _patch_net(monkeypatch, net, _route(("wlan0", 600)))
    assert inventory.read_uplink() == {"uplink_type": "wifi", "uplink_interface": "wlan0"}
    _patch_net(monkeypatch, net, _route(("wwan0", 700)))
    assert inventory.read_uplink()["uplink_type"] == "cellular"
    _patch_net(monkeypatch, net, _route(("wg0", 50)))
    assert inventory.read_uplink() == {"uplink_type": "other", "uplink_interface": "wg0"}


def test_no_default_route_means_no_uplink(monkeypatch, tmp_path) -> None:
    net = _links(tmp_path)
    _patch_net(monkeypatch, net, _route())
    assert inventory.read_uplink() == {}
    _patch_net(monkeypatch, net, None)
    assert inventory.read_uplink() == {}


def test_wifi_is_recognised_without_a_uevent_devtype(monkeypatch, tmp_path) -> None:
    net = _links(tmp_path)
    (net / "wlan0" / "uevent").unlink()
    (net / "wlan0" / "wireless").mkdir()
    _patch_net(monkeypatch, net, _route(("wlan0", 600)))
    assert inventory.read_uplink()["uplink_type"] == "wifi"
