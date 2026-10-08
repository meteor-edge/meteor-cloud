#!/usr/bin/env python3
"""End-to-end smoke test against a deployed MeteorCloud entry point.

Works for any deployment (Compose + Traefik, Kubernetes ingress, EC2):

    python3 scripts/smoke_test.py http://localhost:8080

Checks the public routes, then registers a throwaway user, creates an
organization, uploads an artifact, and downloads it again. That exercises
PostgreSQL and the configured object storage backend. Standard library only.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid


def request(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def expect(label: str, status: int, payload: bytes, wanted: int) -> bytes:
    if status != wanted:
        raise SystemExit(f"FAIL {label}: HTTP {status} (wanted {wanted}): {payload[:400]!r}")
    print(f"OK   {label}: HTTP {status}")
    return payload


def wait_for(url: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if request("GET", url)[0] == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL {url} did not become reachable within {timeout:.0f}s")
        time.sleep(2)


def post_json(url: str, data: dict, token: str | None = None) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return request("POST", url, body=json.dumps(data).encode(), headers=headers)


def multipart(fields: dict[str, str], file_name: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main(base: str) -> None:
    base = base.rstrip("/")
    api = f"{base}/api/v1"
    wait_for(f"{base}/health")

    expect("API health", *request("GET", f"{base}/health"), 200)
    expect("versioned API health", *request("GET", f"{api}/health"), 200)
    console = expect("console", *request("GET", f"{base}/"), 200)
    if b"<html" not in console.lower():
        raise SystemExit("FAIL console: response is not HTML")
    # The entry point must not route private control-plane endpoints.
    status, _ = request("POST", f"{base}/internal/mqtt/authenticate", body=b"{}")
    if status not in (404, 405) and not (200 <= status < 300 and b"<html" in _.lower()):
        raise SystemExit(f"FAIL /internal is reachable through the entry point (HTTP {status})")
    print("OK   /internal not routed to the API")

    email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
    password = secrets.token_urlsafe(18)
    expect("register", *post_json(f"{api}/auth/register", {"email": email, "full_name": "Smoke", "password": password}), 201)
    token = json.loads(expect("login", *post_json(f"{api}/auth/login", {"email": email, "password": password}), 200))[
        "access_token"
    ]
    org = json.loads(expect("create organization", *post_json(f"{api}/organizations", {"name": f"Smoke {email}"}, token), 201))

    content = secrets.token_bytes(256 * 1024)
    body, content_type = multipart({"name": "smoke-image", "version": "1.0.0", "type": "os_image"}, "smoke.img", content)
    artifacts = f"{api}/organizations/{org['id']}/artifacts"
    auth = {"Authorization": f"Bearer {token}"}
    artifact = json.loads(
        expect(
            "upload artifact",
            *request("POST", artifacts, body=body, headers={**auth, "Content-Type": content_type}),
            201,
        )
    )
    downloaded = expect("download artifact", *request("GET", f"{artifacts}/{artifact['id']}/download", headers=auth), 200)
    if downloaded != content:
        raise SystemExit("FAIL download artifact: content differs from upload")
    print("OK   artifact round trip through object storage")
    print("All smoke checks passed.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_test.py <base-url>")
    main(sys.argv[1])
