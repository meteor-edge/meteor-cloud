#!/usr/bin/env python3
"""Verify SDK against a live MeteorCloud deployment.

Usage:
  python scripts/sdk-e2e-verify.py \\
    --base-url https://e2e-....meteor-edge.io \\
    --admin-email ... --admin-password ...

Creates an org application + credential, then publish / query / subscribe / whoami.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sdk" / "python" / "src"))

from meteorcloud import MeteorClient  # noqa: E402


def login(base: str, email: str, password: str) -> str:
    r = httpx.post(f"{base}/api/v1/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True, help="Platform public URL (https://…)")
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password", required=True)
    p.add_argument("--org-name", default="SDK E2E Org")
    args = p.parse_args()
    base = args.base_url.rstrip("/")

    print(f"[1] login {args.admin_email} @ {base}")
    token = login(base, args.admin_email, args.admin_password)
    headers = {"Authorization": f"Bearer {token}"}

    print("[2] create organization")
    org = httpx.post(
        f"{base}/api/v1/organizations",
        headers=headers,
        json={"name": args.org_name, "slug": f"sdk-e2e-{int(time.time())}"},
        timeout=30,
    )
    if org.status_code == 409:
        orgs = httpx.get(f"{base}/api/v1/organizations", headers=headers, timeout=30)
        orgs.raise_for_status()
        org_id = orgs.json()[0]["id"]
    else:
        org.raise_for_status()
        org_id = org.json()["id"]
    print(f"    org_id={org_id}")

    print("[3] create application + credential")
    app = httpx.post(
        f"{base}/api/v1/organizations/{org_id}/applications",
        headers=headers,
        json={
            "slug": "sdk-probe",
            "name": "SDK Probe",
            "permissions": {
                "publish": ["demo.*"],
                "subscribe": ["demo.**"],
                "read": ["demo.**"],
            },
        },
        timeout=30,
    )
    if app.status_code == 409:
        apps = httpx.get(f"{base}/api/v1/organizations/{org_id}/applications", headers=headers, timeout=30)
        apps.raise_for_status()
        app_id = next(a["id"] for a in apps.json() if a["slug"] == "sdk-probe")
    else:
        app.raise_for_status()
        app_id = app.json()["id"]

    cred = httpx.post(
        f"{base}/api/v1/organizations/{org_id}/applications/{app_id}/credentials",
        headers=headers,
        json={"environment": "e2e", "name": "aws-sdk-verify"},
        timeout=30,
    )
    cred.raise_for_status()
    app_token = cred.json()["token"]
    print(f"    application_id={app_id}")

    with tempfile.TemporaryDirectory() as tmp:
        token_file = Path(tmp) / "token"
        token_file.write_text(app_token, encoding="utf-8")
        token_file.chmod(0o600)

        print("[4] SDK whoami / publish / query / subscribe")
        client = MeteorClient(endpoint=base, token_file=str(token_file))
        who = client.whoami()
        print(f"    whoami application={who.application} environment={who.environment}")

        payload = {"power": 1200, "voltage": 230, "ts": int(time.time())}
        result = client.telemetry.publish("demo.metrics", payload)
        print(f"    publish status={result.status} id={result.id}")

        # Wait briefly for durable store (memory bus is immediate; postgres path too)
        deadline = time.time() + 15
        events = []
        while time.time() < deadline:
            events = list(client.telemetry.query("demo.metrics", limit=5, order="desc"))
            if events:
                break
            time.sleep(0.5)
        if not events:
            print("FAIL: query returned no events after publish", file=sys.stderr)
            return 1
        print(f"    query got {len(events)} event(s); latest={events[0].data}")

        seen = []
        with client.telemetry.subscribe("demo.*") as live:
            client.telemetry.publish("demo.metrics", {"power": 1210, "ping": "live"})
            for _ in range(20):
                try:
                    event = live.get(timeout=1.0)
                except TimeoutError:
                    continue
                seen.append(event)
                if event.data.get("ping") == "live":
                    break
        if not seen:
            print("FAIL: subscribe received nothing", file=sys.stderr)
            return 1
        print(f"    subscribe got {len(seen)} event(s); last={seen[-1].data}")
        client.close()

    print("RESULT: PASS")
    print(json.dumps({"org_id": org_id, "application_id": app_id, "publish": result.status}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
