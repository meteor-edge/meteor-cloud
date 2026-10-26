"""Domain parsing and Route53 zone matching."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from edge_installer.deployment import route53
from edge_installer.deployment.route53 import (
    create_a_records,
    list_hosted_zones,
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


def _fake_aws(monkeypatch, *, records: list[dict], zones: list[dict] | None = None) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        if command[2] == "list-resource-record-sets":
            out = {"ResourceRecordSets": records}
        elif command[2] == "list-hosted-zones":
            out = {"HostedZones": zones or []}
        else:
            out = {}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(out), stderr="")

    monkeypatch.setattr(route53.subprocess, "run", run)
    return calls


def test_private_zones_are_skipped(monkeypatch) -> None:
    _fake_aws(
        monkeypatch,
        records=[],
        zones=[
            {"Id": "/hostedzone/ZPRIV", "Name": "example.com.", "Config": {"PrivateZone": True}},
            {"Id": "/hostedzone/ZPUB", "Name": "example.com.", "Config": {"PrivateZone": False}},
        ],
    )
    assert list_hosted_zones() == [("ZPUB", "example.com.")]


def test_create_never_replaces_a_record_that_points_elsewhere(monkeypatch) -> None:
    calls = _fake_aws(
        monkeypatch,
        records=[{"Name": "example.com.", "Type": "A", "ResourceRecords": [{"Value": "198.51.100.7"}]}],
    )
    with pytest.raises(RuntimeError, match="refusing to replace"):
        create_a_records(domain="example.com", zone_id="Z", ip="203.0.113.5", wireguard=False)
    assert not any(command[2] == "change-resource-record-sets" for command in calls)


def test_create_adds_only_missing_records(monkeypatch) -> None:
    calls = _fake_aws(
        monkeypatch,
        records=[{"Name": "\\052.fleet.example.com.", "Type": "A", "ResourceRecords": [{"Value": "203.0.113.5"}]}],
    )
    create_a_records(domain="fleet.example.com", zone_id="Z", ip="203.0.113.5", wireguard=True)
    change = next(command for command in calls if command[2] == "change-resource-record-sets")
    batch = json.loads(change[change.index("--change-batch") + 1])
    assert {item["Action"] for item in batch["Changes"]} == {"CREATE"}
    assert [item["ResourceRecordSet"]["Name"] for item in batch["Changes"]] == [
        "fleet.example.com",
        "wg.fleet.example.com",
    ]
