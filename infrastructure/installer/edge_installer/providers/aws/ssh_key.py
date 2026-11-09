"""Ensure the local PEM matches the EC2 key pair named in installation.yaml.

A mismatched key creates the instance, then fails at SSH — leaving a half-applied
host. Catch that before Terraform runs when the AWS CLI can describe the key.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from edge_installer.process.runner import command_exists, run_command

logger = logging.getLogger(__name__)


def normalize_openssh_public_key(material: str) -> str:
    """Keep type + key body; drop the optional comment."""
    parts = material.strip().split()
    if len(parts) < 2:
        raise ValueError(f"not an OpenSSH public key: {material[:40]!r}")
    return f"{parts[0]} {parts[1]}"


def public_key_from_private(private_key_path: Path) -> str:
    result = run_command(
        ["ssh-keygen", "-y", "-f", str(private_key_path)],
        stream=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ssh-keygen failed").strip()
        raise ValueError(f"cannot read public key from {private_key_path}: {detail}")
    return normalize_openssh_public_key(result.stdout)


def fetch_ec2_key_pair_public_key(
    *,
    key_name: str,
    region: str,
    profile: str | None = None,
) -> str:
    command = [
        "aws",
        "ec2",
        "describe-key-pairs",
        "--key-names",
        key_name,
        "--region",
        region,
        "--include-public-key",
        "--output",
        "json",
    ]
    if profile:
        command.extend(["--profile", profile])
    result = run_command(command, stream=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "aws ec2 describe-key-pairs failed").strip()
        raise ValueError(detail)
    payload = json.loads(result.stdout)
    pairs = payload.get("KeyPairs") or []
    if not pairs:
        raise ValueError(f"EC2 key pair {key_name!r} not found in {region}")
    public = pairs[0].get("PublicKey")
    if not public:
        raise ValueError(
            f"EC2 key pair {key_name!r} has no PublicKey "
            "(need AWS CLI with --include-public-key support)"
        )
    return normalize_openssh_public_key(public)


def validate_ssh_key_matches_ec2(
    *,
    private_key_path: Path,
    key_name: str,
    region: str,
    profile: str | None = None,
) -> list[str]:
    """Return human-readable errors; empty means OK or check skipped."""
    if not command_exists("aws"):
        logger.debug("Skipping EC2 SSH key check: aws CLI not on PATH")
        return []
    if not command_exists("ssh-keygen"):
        return []

    try:
        local = public_key_from_private(private_key_path)
    except ValueError as exc:
        return [str(exc)]

    try:
        remote = fetch_ec2_key_pair_public_key(
            key_name=key_name, region=region, profile=profile
        )
    except ValueError as exc:
        return [
            f"aws.ssh_key_name {key_name!r} in {region}: {exc}. "
            "Import your public key (`aws ec2 import-key-pair`) or fix the name."
        ]

    if local != remote:
        return [
            f"aws.ssh_private_key_path ({private_key_path}) does not match EC2 key pair "
            f"{key_name!r} in {region}. Import this PEM's public key under that name, "
            "or set aws.ssh_key_name to the key pair that matches the PEM."
        ]
    return []
