"""Resolve a Route53 zone from a domain and create its A records (make up --route53).

Records are only created, never overwritten: a name that already points somewhere
else is an error, so a typo or the zone apex cannot replace live DNS.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def normalize_domain(raw: str) -> str:
    value = raw.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    return value


def valid_domain(domain: str) -> bool:
    return bool(domain) and _DOMAIN_RE.fullmatch(domain) is not None


def zone_id_for_domain(domain: str, zones: list[tuple[str, str]]) -> str | None:
    """Longest hosted-zone name that equals domain or is a parent of it."""
    host = normalize_domain(domain)
    matches: list[tuple[int, str]] = []
    for zone_id, name in zones:
        zone_name = name.lower().rstrip(".")
        if host == zone_name or host.endswith(f".{zone_name}"):
            matches.append((len(zone_name), zone_id.removeprefix("/hostedzone/")))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def list_hosted_zones(*, profile: str | None = None) -> list[tuple[str, str]]:
    command = ["aws", "route53", "list-hosted-zones", "--output", "json"]
    if profile:
        command.extend(["--profile", profile])
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("aws CLI is not installed") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "aws route53 failed")
    payload = json.loads(result.stdout)
    zones: list[tuple[str, str]] = []
    for item in payload.get("HostedZones", []):
        if (item.get("Config") or {}).get("PrivateZone"):
            continue
        zone_id = str(item.get("Id", "")).removeprefix("/hostedzone/")
        name = str(item.get("Name", ""))
        if zone_id and name:
            zones.append((zone_id, name))
    return zones


def persist_domain(path, domain: str, zone_id: str) -> None:
    text = path.read_text(encoding="utf-8")
    if domain:
        text, count = re.subn(r"(?m)^([ \t]*domain:\s*)\S.*$", rf"\g<1>{domain}", text, count=1)
        if count == 0:
            logger.warning("Could not write platform.domain into %s", path)
    if zone_id:
        text, count = re.subn(r'(?m)^([ \t]*dns_zone_id:\s*).*$', rf"\g<1>{zone_id}", text, count=1)
        if count == 0:
            logger.warning("Could not write aws.dns_zone_id into %s", path)
    path.write_text(text, encoding="utf-8")


def _aws(command: list[str], profile: str | None) -> str:
    if profile:
        command = [*command, "--profile", profile]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "aws route53 failed")
    return result.stdout


def existing_records(
    zone_id: str, names: list[str], *, profile: str | None = None
) -> dict[str, tuple[str, list[str]]]:
    """name -> (type, values) for the given names that already exist in the zone."""
    found: dict[str, tuple[str, list[str]]] = {}
    for name in names:
        payload = json.loads(
            _aws(
                [
                    "aws", "route53", "list-resource-record-sets",
                    "--hosted-zone-id", zone_id,
                    "--start-record-name", name,
                    "--max-items", "5",
                    "--output", "json",
                ],
                profile,
            )
        )
        for item in payload.get("ResourceRecordSets", []):
            # Route53 escapes "*" as \052 and returns names with a trailing dot.
            item_name = item.get("Name", "").replace("\\052", "*").rstrip(".")
            if item_name == name and item.get("Type") in ("A", "AAAA", "CNAME"):
                values = [record.get("Value", "") for record in item.get("ResourceRecords", [])]
                found[name] = (item["Type"], values)
    return found


def create_a_records(
    *,
    domain: str,
    zone_id: str,
    ip: str,
    wireguard: bool,
    profile: str | None = None,
) -> list[str]:
    """Create the A records that are missing. Refuse names that already point elsewhere."""
    names = [domain, f"*.{domain}"]
    if wireguard:
        names.append(f"wg.{domain}")
    existing = existing_records(zone_id, names, profile=profile)
    conflicts = [
        f"{name} ({kind} {', '.join(values)})"
        for name, (kind, values) in existing.items()
        if (kind, values) != ("A", [ip])
    ]
    if conflicts:
        raise RuntimeError(
            "refusing to replace existing records: " + "; ".join(conflicts)
            + ". Remove them or point them at this server yourself."
        )
    missing = [name for name in names if name not in existing]
    if missing:
        changes = [
            {
                "Action": "CREATE",
                "ResourceRecordSet": {
                    "Name": name,
                    "Type": "A",
                    "TTL": 60,
                    "ResourceRecords": [{"Value": ip}],
                },
            }
            for name in missing
        ]
        _aws(
            [
                "aws", "route53", "change-resource-record-sets",
                "--hosted-zone-id", zone_id,
                "--change-batch", json.dumps({"Changes": changes}),
            ],
            profile,
        )
    return names


def _ipv4(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an IPv4 address: {value}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create Route53 A records for a MeteorCloud domain.")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--ip", required=True, type=_ipv4)
    parser.add_argument("--profile", default="")
    parser.add_argument("--no-wireguard", action="store_true")
    args = parser.parse_args(argv)
    domain = normalize_domain(args.domain)
    if not valid_domain(domain):
        print(f"error: invalid domain: {args.domain}", file=sys.stderr)
        return 2
    try:
        zones = list_hosted_zones(profile=args.profile or None)
        zone_id = zone_id_for_domain(domain, zones)
        if not zone_id:
            names = ", ".join(name.rstrip(".") for _, name in zones) or "(none)"
            print(f"error: no Route53 zone contains {domain}. Hosted zones: {names}", file=sys.stderr)
            return 1
        names = create_a_records(
            domain=domain,
            zone_id=zone_id,
            ip=args.ip,
            wireguard=not args.no_wireguard,
            profile=args.profile or None,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Route53 {zone_id}: {', '.join(names)} -> {args.ip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
