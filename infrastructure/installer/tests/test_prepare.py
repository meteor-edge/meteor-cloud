"""prepare_apply: domain + demo DNS zone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from edge_installer.config.loader import load_configuration
from edge_installer.deployment.prepare import prepare_apply

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "edge_installer"
    / "config"
    / "examples"
    / "installation.yaml"
)


def test_demo_resolves_zone_and_writes_yaml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PLATFORM_DOMAIN", raising=False)
    monkeypatch.delenv("EDGE_PLATFORM_DNS_ZONE_ID", raising=False)
    monkeypatch.setenv("METEORCLOUD_NONINTERACTIVE", "1")
    content = EXAMPLE.read_text(encoding="utf-8").replace("domain: null", "domain: demo.meteor-edge.io")
    content = content.replace("~/.ssh/edge-platform.pem", str(tmp_path / "key.pem"))
    (tmp_path / "key.pem").write_text("k", encoding="utf-8")
    path = tmp_path / "installation.yaml"
    path.write_text(content, encoding="utf-8")
    config = load_configuration(path)
    config.aws.dns_zone_id = ""

    with patch(
        "edge_installer.deployment.prepare.list_hosted_zones",
        return_value=[("ZIO", "meteor-edge.io.")],
    ):
        prepare_apply(config, config_path=path, console=Console(), demo=True)

    assert config.installation.demo is True
    assert config.platform.domain == "demo.meteor-edge.io"
    assert config.aws.dns_zone_id == "ZIO"
    assert "domain: demo.meteor-edge.io" in path.read_text(encoding="utf-8")
    assert "dns_zone_id: ZIO" in path.read_text(encoding="utf-8")


def test_no_demo_does_not_lookup_zone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METEORCLOUD_NONINTERACTIVE", "1")
    monkeypatch.setenv("PLATFORM_DOMAIN", "demo.meteor-edge.io")
    content = EXAMPLE.read_text(encoding="utf-8").replace("~/.ssh/edge-platform.pem", str(tmp_path / "key.pem"))
    (tmp_path / "key.pem").write_text("k", encoding="utf-8")
    path = tmp_path / "installation.yaml"
    path.write_text(content, encoding="utf-8")
    config = load_configuration(path)

    with patch("edge_installer.deployment.prepare.list_hosted_zones") as lookup:
        prepare_apply(config, config_path=path, console=Console(), demo=False)

    lookup.assert_not_called()
    assert config.installation.demo is False
    assert config.aws.dns_zone_id == ""
