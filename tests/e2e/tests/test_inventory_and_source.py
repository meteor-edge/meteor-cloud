import gzip
import json
import tarfile
from pathlib import Path

from e2e.ansible import parse_recap, render_inventory
from e2e.cli import dns_records, hosts_from_outputs, parser
from e2e.config import load
from e2e.source import build_archive
from e2e.state import State

OUTPUTS = {
    "ssh_user": "ubuntu",
    "control_plane": {"instance_id": "i-1", "public_ip": "198.51.100.1", "private_ip": "172.31.0.1"},
    "gateway": {"instance_id": "i-2", "public_ip": "198.51.100.2", "private_ip": "172.31.0.2"},
    "devices": [{"instance_id": "i-3", "public_ip": "198.51.100.3", "private_ip": "172.31.0.3"}],
}


def test_inventory_pins_host_keys_and_keeps_secrets_out(tmp_path: Path) -> None:
    cfg = load(tmp_path / "x", environ={"E2E_ENV_NAME": "ci-1", "E2E_AWS_REGION": "eu-central-1",
                                         "E2E_DNS_PROVIDER": "route53", "E2E_DNS_ZONE_ID": "Z1",
                                         "E2E_DOMAIN": "example.com", "E2E_ACME_EMAIL": "a@example.com",
                                         "E2E_VPN_PLACEMENT": "dedicated"})
    state = State("ci-1", root=tmp_path)
    state.ensure()
    secrets = state.secrets(cfg)
    hosts = hosts_from_outputs(OUTPUTS)
    inventory = render_inventory(cfg, state, hosts, platform_archive=tmp_path / "p.tgz",
                                 device_archive=tmp_path / "d.tgz", ssh_key=tmp_path / "key")
    text = state.inventory.read_text()
    for key, value in secrets.items():
        if "EMAIL" not in key:
            assert value not in text, key
    args = inventory["all"]["vars"]["ansible_ssh_common_args"]
    assert "StrictHostKeyChecking=accept-new" in args and str(state.known_hosts) in args
    platform = inventory["all"]["children"]["platform"]["vars"]
    assert platform["platform_domain"] == "ci-1.example.com"
    assert platform["minio_enabled"] is True
    assert platform["vpn_link_endpoint"] == "172.31.0.2:51820"
    assert platform["traefik_aws_region"] == "eu-central-1"
    assert inventory["all"]["children"]["vpn_gateway"]["hosts"]["vpn-gateway"]["vpn_public_host"] == "wg.ci-1.example.com"
    assert "vpn_public_host" not in inventory["all"]["children"]["platform"]["hosts"]["control-plane"]
    assert (state.secrets_file.stat().st_mode & 0o777) == 0o600
    assert state.secrets(cfg) == secrets  # stable across calls


def test_inventory_uses_the_ip_for_wireguard_without_public_dns(tmp_path: Path) -> None:
    cfg = load(tmp_path / "x", environ={"E2E_ENV_NAME": "ci-1", "E2E_AWS_REGION": "eu-central-1"})
    state = State("ci-1", root=tmp_path)
    state.ensure()
    outputs = {**OUTPUTS, "gateway": None}
    inventory = render_inventory(cfg, state, hosts_from_outputs(outputs), platform_archive=tmp_path / "p.tgz",
                                 device_archive=tmp_path / "d.tgz", ssh_key=tmp_path / "key")
    control_plane = inventory["all"]["children"]["platform"]["hosts"]["control-plane"]
    assert control_plane["vpn_public_host"] == "198.51.100.1"


def test_parse_recap() -> None:
    out = "PLAY RECAP ****\ncontrol-plane : ok=10 changed=0 unreachable=0 failed=0 skipped=3\n" \
          "device-1 : ok=5 changed=2 unreachable=0 failed=1 skipped=0\n"
    assert parse_recap(out) == {
        "control-plane": {"ok": 10, "changed": 0, "unreachable": 0, "failed": 0},
        "device-1": {"ok": 5, "changed": 2, "unreachable": 0, "failed": 1},
    }


def test_archive_is_deterministic(tmp_path: Path) -> None:
    first = build_archive(tmp_path / "a.tgz", subdir="tests/e2e/testapp")
    data = first.read_bytes()
    second = build_archive(tmp_path / "b.tgz", subdir="tests/e2e/testapp")
    assert data == second.read_bytes()
    with tarfile.open(fileobj=gzip.open(first)) as tar:
        assert sorted(tar.getnames()) == ["Dockerfile", "serve.sh"]


def test_cli_commands() -> None:
    p = parser()
    assert p.parse_args(["down", "--yes"]).yes
    assert p.parse_args(["up", "--only", "5 wireguard", "--destroy-on-failure"]).destroy_on_failure
    assert json.dumps(sorted(p._subparsers._group_actions[0].choices)) == json.dumps(
        sorted(["validate", "up", "seed", "test", "status", "logs", "down"]))
    assert p.parse_args(["up", "--no-tests"]).no_tests


def test_forget_replaced_hosts(tmp_path: Path) -> None:
    from e2e.cli import forget_replaced_hosts
    from e2e.state import Host

    state = State("ci-1", root=tmp_path)
    state.ensure()
    state.known_hosts.write_text("198.51.100.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOld\n"
                                 "198.51.100.3 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeep\n")
    before = [Host("control-plane", "control_plane", "198.51.100.1", "u", "", "i-old"),
              Host("device-1", "device", "198.51.100.3", "u", "", "i-3")]
    after = [Host("control-plane", "control_plane", "198.51.100.1", "u", "", "i-new"),
             Host("device-1", "device", "198.51.100.3", "u", "", "i-3")]
    assert forget_replaced_hosts(state, before, after) == ["control-plane"]
    text = state.known_hosts.read_text()
    assert "198.51.100.1" not in text and "198.51.100.3" in text


def test_dns_records_point_wireguard_at_the_gateway(tmp_path: Path) -> None:
    cfg = load(tmp_path / "x", environ={"E2E_ENV_NAME": "demo", "E2E_DOMAIN": "example.com", "E2E_AWS_REGION": "eu-central-1",
                                         "E2E_DNS_PROVIDER": "manual", "E2E_VPN_PLACEMENT": "dedicated"})
    records = {name: ip for name, ip, _ in dns_records(cfg, hosts_from_outputs(OUTPUTS))}
    assert records == {
        "demo.example.com": "198.51.100.1",
        "*.demo.example.com": "198.51.100.1",
        "wg.demo.example.com": "198.51.100.2",
    }
