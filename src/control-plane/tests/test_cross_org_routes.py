"""Every organization-scoped route refuses a member of another organization.

Walks the OpenAPI schema (every included route), so a new route under ``/api/v1/organizations/{organization_id}``
is covered without editing this file. The outsider is a real, signed-in user who owns
organization B; every request targets organization A. Any 2xx (or a crash) is a leak.
"""

from __future__ import annotations

import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import auth_header, create_org_with_owner, create_user

ORG_PREFIX = "/api/v1/organizations/{organization_id}"
PARAM = re.compile(r"{([^}:]+)(?::[^}]+)?}")


def _org_routes(client: TestClient) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for path, operations in client.app.openapi()["paths"].items():
        if not path.startswith(ORG_PREFIX):
            continue
        for method in sorted(operations):
            if method in {"get", "post", "put", "patch", "delete"}:
                routes.append((method.upper(), path))
    return routes


def test_route_table_has_organization_routes(client: TestClient) -> None:
    # Guard against the walk silently covering nothing after a prefix change.
    assert len(_org_routes(client)) > 50


def test_member_of_another_organization_gets_no_success_anywhere(client: TestClient, db_session: Session) -> None:
    owner_a = create_user(db_session, email="owner-a@example.com")
    org_a, _ = create_org_with_owner(db_session, owner_a, slug="org-a")
    owner_b = create_user(db_session, email="owner-b@example.com")
    create_org_with_owner(db_session, owner_b, slug="org-b")
    outsider = auth_header(client, "owner-b@example.com")

    leaks: list[str] = []
    for method, path in _org_routes(client):
        url = PARAM.sub(
            lambda match: str(org_a.id) if match.group(1) == "organization_id" else str(uuid.uuid4()),
            path,
        )
        response = client.request(method, url, headers=outsider, json={})
        if response.status_code < 300 or response.status_code >= 500:
            leaks.append(f"{method} {path} -> {response.status_code}")
    assert not leaks, "Routes that answered another organization's member:\n" + "\n".join(leaks)


def test_api_responses_carry_security_headers(client: TestClient) -> None:
    for response in (client.get("/health"), client.get("/api/v1/auth/me")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["content-security-policy"].startswith("default-src 'none'")
    # Swagger UI loads from a CDN: no restrictive CSP there.
    assert "content-security-policy" not in client.get("/docs").headers
