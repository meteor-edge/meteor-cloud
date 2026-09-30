"""Service registry tests."""

from edge_installer.services.registry import resolve_enabled_services


def test_resolve_enabled_services_orders_dependencies() -> None:
    enabled = {"cloud_app": True, "vpn": True}
    assert resolve_enabled_services(enabled) == ["cloud_app", "vpn"]


def test_vpn_alone_still_lists_vpn() -> None:
    enabled = {"cloud_app": False, "vpn": True}
    assert resolve_enabled_services(enabled) == ["vpn"]
