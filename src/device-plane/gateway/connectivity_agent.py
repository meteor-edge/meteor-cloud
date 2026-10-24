"""Host gateway agent: apply WireGuard peers from the control plane.

Run on the host that owns wg0 (the API host, or a dedicated gateway). Never
stores device private keys. Peers given with --keep-peer (site links to the API
hosts on a dedicated gateway) are never removed.

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
from datetime import datetime, timezone  # no datetime.UTC: the host python3 may be 3.10
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


def _peer_dump(interface: str, *, runner: Runner = _run) -> dict[str, tuple[str, int]]:
    """Public key -> (allowed-ips, latest handshake in unix seconds, 0 = never) for each peer."""
    try:
        dump = runner(["wg", "show", interface, "dump"]).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    peers: dict[str, tuple[str, int]] = {}
    # The first line is the interface itself; peer lines are: key, psk, endpoint, allowed-ips, handshake, ...
    for line in dump.splitlines()[1:]:
        parts = line.split("\t")
        allowed = parts[3] if len(parts) > 3 else ""
        handshake = parts[4] if len(parts) > 4 else ""
        peers[parts[0]] = (allowed, int(handshake) if handshake.isdigit() else 0)
    return peers


def current_peers(interface: str, *, runner: Runner = _run) -> dict[str, int]:
    """Public key -> latest handshake (unix seconds, 0 = never) for each peer on the interface."""
    return {key: handshake for key, (_allowed, handshake) in _peer_dump(interface, runner=runner).items()}


def _same_allowed_ips(left: str, right: str) -> bool:
    def normalize(value: str) -> set[str]:
        return {item.strip() for item in value.split(",") if item.strip() and item.strip() != "(none)"}

    return normalize(left) == normalize(right)


def apply_peers(
    *,
    interface: str,
    desired: list[dict[str, Any]],
    keep: frozenset[str] = frozenset(),
    runner: Runner = _run,
) -> None:
    """Make the interface's device peers match the authorized list: add missing, fix changed, drop the rest."""
    wanted = {str(peer["public_key"]): str(peer["allowed_ips"]) for peer in desired if peer["public_key"] not in keep}
    present = _peer_dump(interface, runner=runner)
    for key in set(present) - set(wanted) - keep:
        runner(["wg", "set", interface, "peer", key, "remove"])
        logger.info("Removed WireGuard peer %s", key[:8])
    for key, allowed in wanted.items():
        if key in present and _same_allowed_ips(present[key][0], allowed):
            continue  # already applied: no `wg set` every cycle for every peer
        runner(["wg", "set", interface, "peer", key, "allowed-ips", allowed])
        logger.info("%s WireGuard peer %s", "Updated" if key in present else "Added", key[:8])


def sync_once(
    *,
    api_url: str,
    token: str,
    interface: str,
    keep: frozenset[str] = frozenset(),
    runner: Runner = _run,
    reported: dict[str, int] | None = None,
) -> None:
    """One reconcile pass. ``reported`` (kept by the caller) skips handshakes the API already has.

    If the API cannot be reached, the peers on the interface are left as they are, so devices
    stay connected through control-plane outages.
    """
    payload = _request(f"{api_url.rstrip('/')}/internal/connectivity/peers", token)
    apply_peers(interface=interface, desired=list(payload.get("peers") or []), keep=keep, runner=runner)
    handshakes = {key: unix for key, unix in current_peers(interface, runner=runner).items() if unix > 0 and key not in keep}
    sent = reported if reported is not None else {}
    changed = {key: unix for key, unix in handshakes.items() if sent.get(key) != unix}
    if changed:
        _request(
            f"{api_url.rstrip('/')}/internal/connectivity/handshakes",
            token,
            method="POST",
            body={
                "items": [
                    {"public_key": key, "last_handshake_at": datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()}
                    for key, unix in changed.items()
                ]
            },
        )
    if reported is not None:
        reported.clear()
        reported.update(handshakes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply MeteorCloud WireGuard peers on the gateway host.")
    parser.add_argument("--api-url", default=os.environ.get("CONTROL_PLANE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("CONNECTIVITY_INTERNAL_TOKEN", ""))
    parser.add_argument("--interface", default="wg0")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument(
        "--keep-peer",
        action="append",
        default=[key for key in os.environ.get("GATEWAY_STATIC_PEERS", "").split(",") if key.strip()],
        help="Static peer public key to leave alone (repeatable; env GATEWAY_STATIC_PEERS, comma-separated)",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    keep = frozenset(key.strip() for key in args.keep_peer)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.token:
        print("CONNECTIVITY_INTERNAL_TOKEN is required", file=sys.stderr)
        return 1
    if args.once:
        sync_once(api_url=args.api_url, token=args.token, interface=args.interface, keep=keep)
        return 0
    reported: dict[str, int] = {}
    while True:
        try:
            sync_once(api_url=args.api_url, token=args.token, interface=args.interface, keep=keep, reported=reported)
        except Exception:
            logger.exception("Peer sync failed")
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
