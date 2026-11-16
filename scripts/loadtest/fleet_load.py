#!/usr/bin/env python3
"""Heartbeat load generator: N devices against one MeteorCloud API (#68).

Standard library only. It registers devices through the real agent API and then sends
heartbeats the way meteorcli does (inventory, capabilities, metrics), spread evenly over
the heartbeat interval. It speaks the protocol directly, so one machine can play
thousands of devices; for agent behaviour use the device simulator instead
(docs/fleet/simulator.md). See docs/capacity.md for how to run it and what it measured.

  # once: an organization, a registration token and N device credentials
  python scripts/loadtest/fleet_load.py setup --server http://localhost:8000 \\
      --email ops@example.com --password '...' --devices 1000 --out devices.json

  # then: heartbeats every 60 s for 5 minutes (or --rate to force a request rate)
  python scripts/loadtest/fleet_load.py run --server http://localhost:8000 \\
      --devices-file devices.json --interval 60 --duration 300

The target needs REGISTRATION_RATE_LIMIT_REQUESTS raised for setup (one source IP
registers every device). Never point it at a production installation.
"""

from __future__ import annotations

import argparse
import heapq
import http.client
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_local = threading.local()


def _connection(server: str) -> http.client.HTTPConnection:
    """One keep-alive connection per worker thread, like one agent per device."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        parts = urllib.parse.urlsplit(server)
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname, parts.port, timeout=30)
        _local.conn = conn
    return conn


def request(server: str, method: str, path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else None
    for attempt in (1, 2):
        conn = _connection(server)
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            break
        except (http.client.HTTPException, OSError):
            conn.close()
            _local.conn = None
            if attempt == 2:
                raise
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = raw.decode(errors="replace")
    return response.status, data


def _mac(index: int) -> str:
    return "02:4c:" + ":".join(f"{byte:02x}" for byte in index.to_bytes(4, "big"))


def inventory(index: int) -> dict[str, Any]:
    return {
        "serial_number": f"LOAD-{index:08d}",
        "mac_addresses": [_mac(index)],
        "hostname": f"load-{index:05d}",
        "os_name": "Debian GNU/Linux",
        "os_version": "12",
        "kernel_version": "6.6.51+rpt-rpi-2712",
        "architecture": "aarch64",
        "hardware_model": "Raspberry Pi 5 Model B Rev 1.0",
        "cpu_model": "ARM Cortex-A76",
        "cpu_cores": 4,
        "memory_mb": 8048,
        "local_ip": f"10.{(index >> 16) & 255}.{(index >> 8) & 255}.{index & 255 or 1}",
    }


def heartbeat_body(index: int) -> dict[str, Any]:
    return {
        **inventory(index),
        "simulated": True,
        "capabilities": {"systemd": True, "docker": True, "docker_compose": True, "wireguard": False, "ssh": True},
        "metrics": {
            "cpu_percent": round(random.uniform(2, 60), 1),
            "memory_percent": round(random.uniform(20, 70), 1),
            "disk_percent": 41.0,
            "temperature_c": round(random.uniform(45, 70), 1),
        },
    }


def cmd_setup(args: argparse.Namespace) -> int:
    status, login = request(args.server, "POST", "/api/v1/auth/login", {"email": args.email, "password": args.password})
    if status != 200:
        print(f"login failed: HTTP {status} {login}", file=sys.stderr)
        return 1
    user_token = login["access_token"]
    org_id = args.org
    if not org_id:
        status, org = request(
            args.server, "POST", "/api/v1/organizations", {"name": f"Load test {int(time.time())}"}, user_token
        )
        if status not in (200, 201):
            print(f"creating the organization failed: HTTP {status} {org}", file=sys.stderr)
            return 1
        org_id = org["id"]
    status, reg = request(
        args.server,
        "POST",
        f"/api/v1/organizations/{org_id}/registration-tokens",
        {"name": "load test", "max_uses": args.devices + args.offset},
        user_token,
    )
    if status not in (200, 201):
        print(f"creating the registration token failed: HTTP {status} {reg}", file=sys.stderr)
        return 1

    def register(index: int) -> dict[str, Any]:
        code, body = request(args.server, "POST", "/api/v1/agent/register", {"token": reg["token"], **inventory(index)})
        if code not in (200, 201):
            raise RuntimeError(f"device {index}: HTTP {code} {body}")
        return {"index": index, "device_id": body["device_id"], "token": body["device_token"]}

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        devices = list(pool.map(register, range(args.offset, args.offset + args.devices)))
    elapsed = time.monotonic() - started
    out = Path(args.out)
    out.write_text(json.dumps({"server": args.server, "organization_id": org_id, "devices": devices}), encoding="utf-8")
    os.chmod(out, 0o600)
    print(f"Registered {len(devices)} devices in {elapsed:.1f}s ({len(devices) / elapsed:.0f}/s) -> {out}")
    return 0


@dataclass
class Results:
    latencies: list[float] = field(default_factory=list)
    lags: list[float] = field(default_factory=list)
    statuses: Counter = field(default_factory=Counter)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))]


def cmd_run(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.devices_file).read_text(encoding="utf-8"))
    devices = data["devices"][: args.limit or None]
    interval = len(devices) / args.rate if args.rate else args.interval
    results = Results()
    start = time.monotonic() + 1.0
    end = start + args.duration
    # Each device keeps its own phase in the interval, as real agents drift apart.
    schedule = [(start + interval * random.random(), position) for position in range(len(devices))]
    heapq.heapify(schedule)

    def beat(position: int, due: float) -> None:
        device = devices[position]
        began = time.monotonic()
        try:
            body = heartbeat_body(device["index"])
            status, _ = request(args.server, "POST", "/api/v1/agent/heartbeat", body, device["token"])
        except Exception as exc:  # noqa: BLE001 — count it and go on
            status = type(exc).__name__
        finished = time.monotonic()
        with results.lock:
            results.statuses[status] += 1
            results.latencies.append(finished - began)
            results.lags.append(began - due)

    print(
        f"{len(devices)} devices, one heartbeat each every {interval:.1f}s "
        f"({len(devices) / interval:.1f}/s) for {args.duration}s, {args.concurrency} connections"
    )
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        while schedule:
            due, position = heapq.heappop(schedule)
            if due >= end:
                break
            wait = due - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            pool.submit(beat, position, due)
            heapq.heappush(schedule, (due + interval, position))
    elapsed = max(time.monotonic() - start, 1e-9)

    total = sum(results.statuses.values())
    ok = results.statuses.get(200, 0)
    lat_ms = [value * 1000 for value in results.latencies]
    lag_ms = [value * 1000 for value in results.lags]
    summary = {
        "devices": len(devices),
        "target_rps": round(len(devices) / interval, 1),
        "achieved_rps": round(total / elapsed, 1),
        "requests": total,
        "ok": ok,
        "errors": {str(key): count for key, count in results.statuses.items() if key != 200},
        "latency_ms": {
            "p50": round(_percentile(lat_ms, 50), 1),
            "p95": round(_percentile(lat_ms, 95), 1),
            "p99": round(_percentile(lat_ms, 99), 1),
            "max": round(max(lat_ms), 1) if lat_ms else None,
            "mean": round(statistics.fmean(lat_ms), 1) if lat_ms else None,
        },
        # Time a heartbeat waited for a free connection here: high values mean the
        # generator, not the API, is the limit; raise --concurrency.
        "client_lag_p95_ms": round(_percentile(lag_ms, 95), 1),
    }
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(summary) + "\n", encoding="utf-8")
    return 0 if ok == total else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Register N load-test devices and save their credentials.")
    setup.add_argument("--server", required=True)
    setup.add_argument("--email", required=True)
    setup.add_argument("--password", required=True)
    setup.add_argument("--org", help="Existing organization id (default: create one)")
    setup.add_argument("--devices", type=int, required=True)
    setup.add_argument("--offset", type=int, default=0, help="First device number (to add more later)")
    setup.add_argument("--concurrency", type=int, default=16)
    setup.add_argument("--out", default="devices.json")
    setup.set_defaults(func=cmd_setup)

    run = sub.add_parser("run", help="Send heartbeats from the saved devices.")
    run.add_argument("--server", required=True)
    run.add_argument("--devices-file", default="devices.json")
    run.add_argument("--limit", type=int, default=0, help="Use only the first N devices")
    run.add_argument("--interval", type=float, default=60.0, help="Heartbeat interval per device (s)")
    run.add_argument("--rate", type=float, default=0.0, help="Force this many heartbeats per second in total")
    run.add_argument("--duration", type=float, default=120.0)
    run.add_argument("--concurrency", type=int, default=64)
    run.add_argument("--json", help="Also write the summary to this file")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
