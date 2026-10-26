from pathlib import Path

import pytest

from e2e.config import ConfigError, check_settings, load, parse_env_file

BASE = {"E2E_ENV_NAME": "ci-123", "E2E_AWS_REGION": "eu-central-1", "E2E_DNS_PROVIDER": "none"}


def test_parse_env_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# comment\nexport E2E_ENV_NAME='abc'\nE2E_DOMAIN=\"example.com\"\n\nE2E_X=a=b\n")
    assert parse_env_file(path) == {"E2E_ENV_NAME": "abc", "E2E_DOMAIN": "example.com", "E2E_X": "a=b"}


def test_environment_overrides_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("E2E_ENV_NAME=from-file\nE2E_AWS_REGION=us-east-1\nE2E_DNS_PROVIDER=none\n")
    cfg = load(path, environ={"E2E_ENV_NAME": "from-env"})
    assert cfg.env_name == "from-env"
    assert cfg.aws_region == "us-east-1"


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "::/0"])
def test_rejects_wildcard_ssh(tmp_path: Path, cidr: str) -> None:
    with pytest.raises(ConfigError, match="no wildcard SSH"):
        load(tmp_path / "missing", environ={**BASE, "E2E_ALLOWED_SSH_CIDRS": cidr})


def test_rejects_bad_env_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="E2E_ENV_NAME"):
        load(tmp_path / "missing", environ={**BASE, "E2E_ENV_NAME": "Bad_Name"})


def test_route53_needs_zone_and_domain(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as error:
        load(tmp_path / "missing", environ={**BASE, "E2E_DNS_PROVIDER": "route53"})
    assert "E2E_DNS_ZONE_ID" in str(error.value)
    with pytest.raises(ConfigError, match="E2E_ACME_EMAIL"):
        load(tmp_path / "missing", environ={**BASE, "E2E_DNS_PROVIDER": "route53", "E2E_DNS_ZONE_ID": "Z1",
                                            "E2E_DOMAIN": "example.com", "E2E_ACME_EMAIL": "not-an-email"})


def test_domain_settings(tmp_path: Path) -> None:
    cfg = load(tmp_path / "missing", environ={**BASE, "E2E_DNS_PROVIDER": "route53", "E2E_DNS_ZONE_ID": "Z1",
                                              "E2E_DOMAIN": "Example.com.", "E2E_ACME_EMAIL": "ops@example.com"})
    assert cfg.fqdn == "ci-123.example.com"
    assert cfg.apps_base_domain == "ci-123.example.com"
    assert cfg.has_public_dns


def test_no_domain_uses_internal_apps_domain(tmp_path: Path) -> None:
    cfg = load(tmp_path / "missing", environ=BASE)
    assert not cfg.has_public_dns
    assert cfg.apps_base_domain == "ci-123.e2e.internal"


def test_onprem_hosts(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("x")
    cfg = load(tmp_path / "missing", environ={
        "E2E_ENV_NAME": "lab-1", "E2E_PROVIDER": "onprem", "E2E_DNS_PROVIDER": "none",
        "E2E_ONPREM_CONTROL_PLANE": "ubuntu@192.0.2.10", "E2E_ONPREM_GATEWAY": "ubuntu@192.0.2.11,10.0.0.11",
        "E2E_ONPREM_DEVICES": "pi@192.0.2.20; pi@192.0.2.21", "E2E_SSH_KEY_FILE": str(key),
    })
    assert cfg.device_count == 2
    assert cfg.dedicated_gateway
    assert cfg.onprem_gateway.private_ip == "10.0.0.11"
    assert [d.user for d in cfg.onprem_devices] == ["pi", "pi"]
    assert check_settings(cfg) == []
