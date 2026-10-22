"""Host gateway agent: apply WireGuard peers from the control plane.

Run on the cloud host that owns wg0. Never stores device private keys.

  CONNECTIVITY_INTERNAL_TOKEN=... python connectivity_agent.py \\
      --api-url http://127.0.0.1:8000 --interface wg0
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger("meteorcloud.gateway")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=30)


def _request(url: str, token: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Connectivity-Internal-Token": token,
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def current_peer_keys(interface: str, *, runner: Runner = _run) -> set[str]:
    try:
        dump = runner(["wg", "show", interface, "dump"]).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    keys: set[str] = set()
    for index, line in enumerate(dump.splitlines()):
        if index == 0:
            continue
        parts = line.split("\t")
        if parts:
            keys.add(parts[0])
    return keys


def handshake_times(interface: str, *, runner: Runner = _run) -> list[dict[str, Any]]:
    try:
        dump = runner(["wg", "show", interface, "dump"]).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    items: list[dict[str, Any]] = []
    for index, line in enumerate(dump.splitlines()):
        if index == 0:
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        try:
            unix = int(parts[4])
        except ValueError:
            continue
        if unix <= 0:
            continue
        from datetime import UTC, datetime

        items.append(
            {
                "public_key": parts[0],
                "last_handshake_at": datetime.fromtimestamp(unix, tz=UTC).isoformat(),
            }
        )
    return items


def apply_peers(
    *,
    interface: str,
    desired: list[dict[str, Any]],
    runner: Runner = _run,
) -> None:
    wanted = {str(peer["public_key"]): str(peer["allowed_ips"]) for peer in desired}
    present = current_peer_keys(interface, runner=runner)
    for key in present - set(wanted):
        runner(["wg", "set", interface, "peer", key, "remove"])
        logger.info("Removed WireGuard peer %s", key[:8])
    for key, allowed in wanted.items():
        runner(["wg", "set", interface, "peer", key, "allowed-ips", allowed])
        if key not in present:
            logger.info("Added WireGuard peer %s", key[:8])


def sync_once(
    *,
    api_url: str,
    token: str,
    interface: str,
    runner: Runner = _run,
) -> None:
    payload = _request(f"{api_url.rstrip('/')}/internal/connectivity/peers", token)
    apply_peers(interface=interface, desired=list(payload.get("peers") or []), runner=runner)
    items = handshake_times(interface, runner=runner)
    if items:
        _request(
            f"{api_url.rstrip('/')}/internal/connectivity/handshakes",
            token,
            method="POST",
            body={"items": items},
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply MeteorCloud WireGuard peers on the gateway host.")
    parser.add_argument("--api-url", default=os.environ.get("CONTROL_PLANE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("CONNECTIVITY_INTERNAL_TOKEN", ""))
    parser.add_argument("--interface", default="wg0")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.token:
        print("CONNECTIVITY_INTERNAL_TOKEN is required", file=sys.stderr)
        return 1
    if args.once:
        sync_once(api_url=args.api_url, token=args.token, interface=args.interface)
        return 0
    while True:
        try:
            sync_once(api_url=args.api_url, token=args.token, interface=args.interface)
        except Exception:
            logger.exception("Peer sync failed")
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
