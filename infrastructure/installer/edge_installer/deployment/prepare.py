"""Ask for the domain and, for --demo, pick the Route53 zone so apply can create records."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rich.console import Console

from edge_installer.config.models import InstallationConfig
from edge_installer.deployment.route53 import (
    list_hosted_zones,
    normalize_domain,
    persist_domain,
    valid_domain,
    zone_id_for_domain,
)
from edge_installer.exceptions import ConfigurationError


def can_prompt() -> bool:
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and not os.environ.get("METEORCLOUD_NONINTERACTIVE")
    )


def prepare_apply(
    config: InstallationConfig,
    *,
    config_path: Path,
    console: Console,
    demo: bool | None,
) -> None:
    if demo is True:
        config.installation.demo = True
    elif demo is False:
        config.installation.demo = False

    domain = normalize_domain(os.environ.get("PLATFORM_DOMAIN") or config.platform.domain or "")
    if can_prompt():
        hint = domain or "empty = http://<public-ip>"
        entered = console.input(f"Domain (console + API) [{hint}]: ").strip()
        if entered:
            domain = normalize_domain(entered)
    if domain and not valid_domain(domain):
        raise ConfigurationError(f"Invalid domain: {domain}", stage="validation")
    config.platform.domain = domain or None

    if not config.installation.demo or config.aws is None:
        return
    if not domain:
        console.print("No domain: demo organization only. Console will be http://<public-ip>.")
        return

    zone_id = (
        os.environ.get("EDGE_PLATFORM_DNS_ZONE_ID", "").strip()
        or config.aws.dns_zone_id.strip()
    )
    if not zone_id:
        try:
            zones = list_hosted_zones(profile=config.aws.profile)
        except RuntimeError as exc:
            raise ConfigurationError(
                f"--demo needs Route53 ({exc}). Set aws.dns_zone_id or install the AWS CLI.",
                stage="validation",
            ) from exc
        zone_id = zone_id_for_domain(domain, zones) or ""
        if not zone_id:
            names = ", ".join(name.rstrip(".") for _, name in zones) or "(none)"
            raise ConfigurationError(
                f"No Route53 hosted zone contains {domain}. Zones: {names}",
                stage="validation",
            )
    config.aws.dns_zone_id = zone_id
    persist_domain(config_path, domain, zone_id)
    console.print(
        f"Demo DNS: Route53 {zone_id} will get {domain}, *.{domain}, wg.{domain}"
    )
