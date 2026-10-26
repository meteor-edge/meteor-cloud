"""Settings from tests/e2e/.env (or the environment) and prerequisite checks."""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = E2E_DIR.parent.parent
ENV_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,23}$")
DOMAIN_RE = re.compile(r"^(?=.{4,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class ConfigError(Exception):
    """Invalid or missing settings."""


@dataclass(frozen=True)
class OnPremHost:
    name: str
    address: str
    user: str
    private_ip: str = ""


@dataclass
class Config:
    env_name: str
    provider: str = "aws"
    aws_region: str = ""
    vpn_placement: str = "same_host"
    device_count: int = 2
    allowed_ssh_cidrs: list[str] = field(default_factory=list)
    dns_provider: str = "route53"
    dns_zone_id: str = ""
    domain: str = ""
    acme_email: str = ""
    control_plane_instance_type: str = "t3.medium"
    gateway_instance_type: str = "t3.micro"
    device_instance_type: str = "t3.micro"
    # On-prem: existing hosts reached with E2E_SSH_KEY_FILE.
    onprem_control_plane: OnPremHost | None = None
    onprem_gateway: OnPremHost | None = None
    onprem_devices: list[OnPremHost] = field(default_factory=list)
    onprem_ingress_ip: str = ""
    ssh_key_file: str = ""
    # Extra CA bundle for verifying HTTPS (e.g. an on-prem internal CA). Never disables verification.
    ca_file: str = ""

    @property
    def fqdn(self) -> str:
        return f"{self.env_name}.{self.domain}" if self.domain else ""

    @property
    def apps_base_domain(self) -> str:
        # Without a domain, endpoints still get names (never routable publicly).
        return self.fqdn or f"{self.env_name}.e2e.internal"

    @property
    def has_public_dns(self) -> bool:
        return bool(self.domain) and self.dns_provider in ("route53", "manual")

    @property
    def dedicated_gateway(self) -> bool:
        return self.vpn_placement == "dedicated"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        if "=" not in line:
            raise ConfigError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _host(spec: str, name: str) -> OnPremHost:
    # user@address[,private_ip]
    address, _, private_ip = spec.partition(",")
    user, sep, host = address.rpartition("@")
    if not sep or not user or not host:
        raise ConfigError(f"{name}: expected user@host[,private_ip], got {spec!r}")
    return OnPremHost(name=name, address=host, user=user, private_ip=private_ip)


def detect_public_cidr(timeout: float = 5.0) -> str:
    """The operator's public IPv4 as a /32 (for the SSH allow-list)."""
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=timeout) as response:
        address = response.read().decode().strip()
    ipaddress.IPv4Address(address)
    return f"{address}/32"


def load(env_file: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    values = parse_env_file(env_file or E2E_DIR / ".env")
    values.update({k: v for k, v in (os.environ if environ is None else environ).items() if k.startswith("E2E_")})
    if environ is None:
        values.setdefault("AWS_REGION", os.environ.get("AWS_REGION", ""))

    def get(key: str, default: str = "") -> str:
        return values.get(key, default).strip()

    env_name = get("E2E_ENV_NAME")
    if not ENV_NAME_RE.match(env_name):
        raise ConfigError("E2E_ENV_NAME must be 3-24 characters: lowercase letters, digits, hyphens")
    try:
        device_count = int(get("E2E_DEVICE_COUNT", "2"))
    except ValueError as exc:
        raise ConfigError("E2E_DEVICE_COUNT must be a number") from exc

    cfg = Config(
        env_name=env_name,
        provider=get("E2E_PROVIDER", "aws"),
        aws_region=get("E2E_AWS_REGION") or get("AWS_REGION"),
        vpn_placement=get("E2E_VPN_PLACEMENT", "same_host"),
        device_count=device_count,
        allowed_ssh_cidrs=[c.strip() for c in get("E2E_ALLOWED_SSH_CIDRS").split(",") if c.strip()],
        dns_provider=get("E2E_DNS_PROVIDER", "route53" if get("E2E_DNS_ZONE_ID") else "none"),
        dns_zone_id=get("E2E_DNS_ZONE_ID"),
        domain=get("E2E_DOMAIN").lower().rstrip("."),
        acme_email=get("E2E_ACME_EMAIL"),
        control_plane_instance_type=get("E2E_CONTROL_PLANE_INSTANCE_TYPE", "t3.medium"),
        gateway_instance_type=get("E2E_GATEWAY_INSTANCE_TYPE", "t3.micro"),
        device_instance_type=get("E2E_DEVICE_INSTANCE_TYPE", "t3.micro"),
        onprem_ingress_ip=get("E2E_ONPREM_INGRESS_IP"),
        ssh_key_file=get("E2E_SSH_KEY_FILE"),
        ca_file=get("E2E_CA_FILE"),
    )
    if cfg.provider == "onprem":
        if get("E2E_ONPREM_CONTROL_PLANE"):
            cfg.onprem_control_plane = _host(get("E2E_ONPREM_CONTROL_PLANE"), "control-plane")
        if get("E2E_ONPREM_GATEWAY"):
            cfg.onprem_gateway = _host(get("E2E_ONPREM_GATEWAY"), "gateway")
        cfg.onprem_devices = [
            _host(spec.strip(), f"device-{i}")
            for i, spec in enumerate(get("E2E_ONPREM_DEVICES").split(";"), 1)
            if spec.strip()
        ]
        cfg.device_count = len(cfg.onprem_devices)
        cfg.vpn_placement = "dedicated" if cfg.onprem_gateway else "same_host"
    errors = check_settings(cfg)
    if errors:
        raise ConfigError("\n".join(errors))
    return cfg


def check_settings(cfg: Config) -> list[str]:
    """Static validation (no network)."""
    errors: list[str] = []
    if cfg.provider not in ("aws", "onprem"):
        errors.append("E2E_PROVIDER must be aws or onprem")
    if cfg.vpn_placement not in ("same_host", "dedicated"):
        errors.append("E2E_VPN_PLACEMENT must be same_host or dedicated")
    if cfg.dns_provider not in ("route53", "manual", "none"):
        errors.append("E2E_DNS_PROVIDER must be route53, manual or none")
    if cfg.domain and not DOMAIN_RE.match(cfg.domain):
        errors.append(f"E2E_DOMAIN is not a valid domain: {cfg.domain!r}")
    if cfg.dns_provider == "route53" and not (cfg.dns_zone_id and cfg.domain):
        errors.append("E2E_DNS_PROVIDER=route53 needs E2E_DNS_ZONE_ID and E2E_DOMAIN")
    if cfg.dns_provider == "manual" and not cfg.domain:
        errors.append("E2E_DNS_PROVIDER=manual needs E2E_DOMAIN (you create the records)")
    # Optional: Let's Encrypt accounts need no contact address.
    if cfg.acme_email and "@" not in cfg.acme_email:
        errors.append(f"E2E_ACME_EMAIL is not an email address: {cfg.acme_email!r}")
    for cidr in cfg.allowed_ssh_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            errors.append(f"E2E_ALLOWED_SSH_CIDRS: invalid network {cidr!r}")
            continue
        if network.prefixlen == 0:
            errors.append("E2E_ALLOWED_SSH_CIDRS must not contain 0.0.0.0/0 or ::/0 (no wildcard SSH)")
    if cfg.provider == "aws":
        if not cfg.aws_region:
            errors.append("E2E_AWS_REGION (or AWS_REGION) is required for E2E_PROVIDER=aws")
        if not 1 <= cfg.device_count <= 5:
            errors.append("E2E_DEVICE_COUNT must be 1-5")
    else:
        if cfg.onprem_control_plane is None:
            errors.append("E2E_ONPREM_CONTROL_PLANE=user@host is required for E2E_PROVIDER=onprem")
        if not cfg.onprem_devices:
            errors.append("E2E_ONPREM_DEVICES=user@host[;user@host] is required for E2E_PROVIDER=onprem")
        if not cfg.ssh_key_file:
            errors.append("E2E_SSH_KEY_FILE is required for E2E_PROVIDER=onprem")
        if cfg.dns_provider == "route53" and not cfg.onprem_ingress_ip:
            errors.append("E2E_ONPREM_INGRESS_IP is required to manage DNS for on-prem")
    if cfg.ca_file and not Path(cfg.ca_file).is_file():
        errors.append(f"E2E_CA_FILE does not exist: {cfg.ca_file}")
    return errors


def required_tools(cfg: Config) -> list[str]:
    tools = ["ansible-playbook", "ssh", "scp", "ssh-keygen", "git"]
    if cfg.provider == "aws" or cfg.dns_provider == "route53":
        tools.append("terraform")
    return tools


def check_prerequisites(cfg: Config) -> tuple[list[str], list[str]]:
    """Live checks: tools, AWS identity, DNS zone ownership. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    for tool in required_tools(cfg):
        if shutil.which(tool) is None:
            errors.append(f"required tool not found on PATH: {tool}")
    if shutil.which("ansible-galaxy"):
        listed = subprocess.run(["ansible-galaxy", "collection", "list", "community.general"],
                                capture_output=True, text=True, check=False)
        if "community.general" not in listed.stdout:
            errors.append("Ansible collection community.general is missing: "
                          "ansible-galaxy collection install -r deploy/ansible/requirements.yml")
    needs_aws = cfg.provider == "aws" or cfg.dns_provider == "route53"
    if needs_aws:
        if shutil.which("aws") is None:
            warnings.append("aws CLI not found: AWS identity and the Route53 zone are not pre-checked")
        else:
            identity = _aws(cfg, "sts", "get-caller-identity", "--query", "Account", "--output", "text")
            if identity.returncode != 0:
                errors.append("AWS credentials are not usable: " + identity.stderr.strip()[-300:])
            elif cfg.dns_provider == "route53":
                zone = _aws(cfg, "route53", "get-hosted-zone", "--id", cfg.dns_zone_id,
                            "--query", "HostedZone.Name", "--output", "text")
                if zone.returncode != 0:
                    errors.append(f"Route53 zone {cfg.dns_zone_id} is not accessible: {zone.stderr.strip()[-300:]}")
                elif zone.stdout.strip().rstrip(".") != cfg.domain:
                    errors.append(
                        f"Route53 zone {cfg.dns_zone_id} is {zone.stdout.strip()!r}, not E2E_DOMAIN={cfg.domain!r}"
                    )
                else:
                    problem = delegation_problem(cfg)
                    if problem:
                        errors.append(problem)
    if not cfg.has_public_dns:
        warnings.append(
            "no public domain (E2E_DOMAIN/E2E_DNS_PROVIDER): DNS, HTTPS and public app tests will be not_supported"
        )
    if cfg.device_count < 2:
        warnings.append("one device: second-device and device-isolation tests will be BLOCKED")
    if cfg.provider == "aws" and not cfg.allowed_ssh_cidrs:
        warnings.append("E2E_ALLOWED_SSH_CIDRS not set: your current public IP/32 will be used")
    return errors, warnings


def delegation_problem(cfg: Config) -> str:
    """The public delegation of E2E_DOMAIN must point at the Route53 zone, or nothing resolves."""
    zone = _aws(cfg, "route53", "get-hosted-zone", "--id", cfg.dns_zone_id,
                "--query", "DelegationSet.NameServers", "--output", "text")
    if zone.returncode != 0 or shutil.which("dig") is None:
        return ""
    expected = {ns.lower().rstrip(".") for ns in zone.stdout.split()}
    public = subprocess.run(["dig", "+short", "NS", cfg.domain, "@1.1.1.1"], capture_output=True, text=True,
                            timeout=20, check=False)
    actual = {ns.lower().rstrip(".") for ns in public.stdout.split()}
    if not actual or actual != expected:
        return (f"{cfg.domain} is delegated to {sorted(actual) or 'nothing'}, not to Route53 zone "
                f"{cfg.dns_zone_id} ({sorted(expected)}); its records will not resolve publicly")
    return ""


def _aws(cfg: Config, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if cfg.aws_region:
        env["AWS_REGION"] = cfg.aws_region
    return subprocess.run(["aws", *args], capture_output=True, text=True, env=env, timeout=60, check=False)
