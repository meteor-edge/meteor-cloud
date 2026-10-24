"""Domain parsing and Route53 zone matching."""

from __future__ import annotations

from pathlib import Path

from edge_installer.deployment.route53 import (
    normalize_domain,
    persist_domain,
    valid_domain,
    zone_id_for_domain,
)


def test_normalize_domain() -> None:
    assert normalize_domain("https://Demo.Meteor-Edge.io/path") == "demo.meteor-edge.io"
    assert normalize_domain("fleet.example.com.") == "fleet.example.com"


def test_valid_domain() -> None:
    assert valid_domain("demo.meteor-edge.io")
    assert not valid_domain("localhost")
    assert not valid_domain("")


def test_zone_id_picks_longest_parent() -> None:
    zones = [
        ("ZIO", "meteor-edge.io."),
        ("ZCOM", "meteor-edge.com."),
    ]
    assert zone_id_for_domain("demo.meteor-edge.io", zones) == "ZIO"
    assert zone_id_for_domain("meteor-edge.io", zones) == "ZIO"
    assert zone_id_for_domain("other.example.com", zones) is None


def test_persist_domain_rewrites_yaml(tmp_path: Path) -> None:
    path = tmp_path / "installation.yaml"
    path.write_text(
        "platform:\n  domain: null\naws:\n  dns_zone_id: \"\"\n",
        encoding="utf-8",
    )
    persist_domain(path, "demo.meteor-edge.io", "Z01257273R31PYCK7HDEK")
    text = path.read_text(encoding="utf-8")
    assert "domain: demo.meteor-edge.io" in text
    assert "dns_zone_id: Z01257273R31PYCK7HDEK" in text
