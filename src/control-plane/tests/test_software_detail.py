"""Software detail: snapshots, health history, and restart / stop / start of one workload."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.dependencies import get_data_plane_api
from app.audit.models import AuditEvent
from app.core.config import get_settings
from app.deployments.software_models import DeviceSoftware
from app.device_actions.models import DeviceAction
from app.devices.software_history import HealthChange, health_segments
from app.history.models import PlatformEventOutbox
from app.mqtt import runtime
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_org_with_owner, create_user
from tests.test_deployments import _register_device, _setup_release

KEY = "camera-agent.service"
KINDS = ["workload_restart", "workload_stop", "workload_start", "workload_snapshot"]


class _Publisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, **_: object) -> None:
        self.sent.append((topic, payload))


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> _Publisher:
    publisher = _Publisher()
    monkeypatch.setattr(runtime, "_publisher", publisher)
    return publisher


def _snapshot(key: str = KEY, **extra) -> dict:
    return {
        "snapshot": {
            "workload_key": key,
            "kind": "systemd",
            "taken_at": "2026-11-24T10:00:00Z",
            "managed": True,
            "unit": {"name": key, "active_state": "active", "environment": "SECRET=1"},
            **extra,
        }
    }


class _Device:
    """An organization with one device that runs one workload."""

    def __init__(self, client: TestClient, db: Session, kinds: list[str] | None = KINDS) -> None:
        self.client, self.db = client, db
        self.org, self.headers, token, self.release = _setup_release(client, db)
        self.reg = _register_device(client, token, name="kiosk", mac="aa:bb:cc:dd:21:01")
        self.agent = {"Authorization": f"Bearer {self.reg['device_token']}"}
        self.base = f"/api/v1/organizations/{self.org.id}/devices/{self.reg['device_id']}/software"
        created = client.post(
            f"/api/v1/organizations/{self.org.id}/deployments",
            headers=self.headers,
            json={"release_id": self.release["id"], "target": {"device_ids": [self.reg["device_id"]]}},
        )
        assert created.status_code in (200, 201), created.text
        self.heartbeat(capabilities={"systemd": True, **({"actions": kinds} if kinds is not None else {})})
        self.report()
        self.id = client.get(self.base, headers=self.headers).json()[0]["id"]
        self.url = f"{self.base}/{self.id}"

    def heartbeat(self, **body) -> None:
        response = self.client.post("/api/v1/agent/heartbeat", headers=self.agent, json=body)
        assert response.status_code == 200, response.text

    def report(self, health: str = "healthy", **extra) -> None:
        item = {
            "workload_key": KEY,
            "installed_release_id": self.release["id"],
            "installed_version": self.release["version"],
            "health": health,
            "sync_status": "in_sync",
            "progress_percent": 100,
            **extra,
        }
        self.heartbeat(software_report=[item])

    def order(self, kind: str, headers: dict | None = None, **body):
        return self.client.post(f"{self.url}/actions", headers=headers or self.headers, json={"kind": kind, **body})

    def member(self, role: OrganizationRole) -> dict:
        email = f"{role.value}@example.com"
        add_member(self.db, self.org, create_user(self.db, email=email), role)
        return auth_header(self.client, email)

    def collect(self) -> list[dict]:
        return self.client.get("/api/v1/agent/actions", headers=self.agent).json()["actions"]

    def done(self, action_id: str, state: str = "done") -> None:
        response = self.client.post(
            "/api/v1/agent/actions/report",
            headers=self.agent,
            json={"results": [{"id": action_id, "state": state}]},
        )
        assert response.json() == {"accepted": 1}

    def detail(self, headers: dict | None = None) -> dict:
        response = self.client.get(self.url, headers=headers or self.headers)
        assert response.status_code == 200, response.text
        return response.json()


# ------------------------------------------------------------------ snapshots


def test_snapshot_is_stored_without_unknown_fields(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    assert d.detail()["snapshot"] is None
    stored = client.post("/api/v1/agent/software/snapshots", headers=d.agent, json=_snapshot(extra_field="x"))
    assert stored.status_code == 200 and stored.json() == {"accepted": True}
    body = d.detail()
    assert body["snapshot"]["unit"]["active_state"] == "active"
    assert "SECRET" not in str(body) and "extra_field" not in str(body)
    assert body["snapshot_at"] and body["can_manage"] is True
    assert body["supported_actions"] == sorted(KINDS)


def test_snapshot_of_an_unknown_workload_is_ignored(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    stored = client.post("/api/v1/agent/software/snapshots", headers=d.agent, json=_snapshot("other.service"))
    assert stored.json() == {"accepted": False}


def test_snapshot_limits(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    url = "/api/v1/agent/software/snapshots"
    many = [{"name": "c", "state": "running"}] * 21
    assert client.post(url, headers=d.agent, json=_snapshot(containers=many)).status_code == 422
    mounts = [{"type": "bind", "destination": "d" * 512, "source": "s" * 512}] * 64
    big = [{"name": "c", "state": "running", "mounts": mounts}] * 2
    assert client.post(url, headers=d.agent, json=_snapshot(containers=big)).status_code == 413
    assert client.post(url, json=_snapshot()).status_code == 401


def test_snapshot_readings(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    stats = {"name": "web", "cpu_percent": 1.5, "memory_bytes": 2048, "pids": 3}
    sent = _snapshot(
        containers=[
            {"name": "web", "state": "running", "restart_policy": "always", "memory_limit": 4096, "stats": stats}
        ],
        volumes=[{"name": "data", "size": 42}],
        networks=[{"name": "n", "subnets": ["172.20.0.0/16"], "gateways": ["172.20.0.1"]}],
        disk={"taken_at": "2026-11-24T10:00:00Z", "items": [{"type": "Images", "size": 9, "reclaimable": 7}]},
    )
    assert client.post("/api/v1/agent/software/snapshots", headers=d.agent, json=sent).json() == {"accepted": True}
    snapshot = d.detail()["snapshot"]
    assert snapshot["containers"][0]["stats"]["cpu_percent"] == 1.5
    assert snapshot["containers"][0]["memory_limit"] == 4096
    assert snapshot["volumes"][0]["size"] == 42 and snapshot["networks"][0]["subnets"] == ["172.20.0.0/16"]
    assert snapshot["disk"]["items"][0]["reclaimable"] == 7


# ------------------------------------------------------------------ access


def test_other_organizations_and_devices_see_nothing(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    outsider = create_user(db_session, email="outsider@example.com")
    other, _ = create_org_with_owner(db_session, outsider, slug="other-org", name="Other")
    headers = auth_header(client, "outsider@example.com")
    for suffix in ("", "/history", "/actions"):
        assert client.get(d.url + suffix, headers=headers).status_code in (403, 404)
        moved = d.url.replace(str(d.org.id), str(other.id)) + suffix
        assert client.get(moved, headers=headers).status_code == 404
    assert d.order("workload_stop", headers).status_code in (403, 404)
    assert client.get(f"{d.base}/{uuid.uuid4()}", headers=d.headers).status_code == 404


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (OrganizationRole.ADMIN, True),
        (OrganizationRole.OPERATOR, True),
        (OrganizationRole.DEVELOPER, False),
        (OrganizationRole.VIEWER, False),
    ],
)
def test_who_may_manage(client: TestClient, db_session: Session, role: OrganizationRole, allowed: bool) -> None:
    d = _Device(client, db_session)
    headers = d.member(role)
    assert d.detail(headers)["can_manage"] is allowed
    response = d.order("workload_restart", headers)
    assert response.status_code == (200 if allowed else 403), response.text
    # Looking again is reading.
    assert d.order("workload_snapshot", headers).status_code == 200
    if not allowed:
        denied = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "access.denied")).all()
        assert len(denied) == 1


# ------------------------------------------------------------------ actions


def test_stop_and_start(client: TestClient, db_session: Session, broker: _Publisher) -> None:
    d = _Device(client, db_session)
    broker.sent.clear()
    ordered = d.order("workload_stop")
    assert ordered.status_code == 200, ordered.text
    assert broker.sent == [(f"devices/{d.reg['device_id']}/commands", '{"type": "sync_actions"}')]
    [action] = d.collect()
    assert (action["kind"], action["workload_key"], action["service"]) == ("workload_stop", KEY, None)
    d.done(action["id"])
    body = d.detail()
    assert body["stopped_by_user"] is True and body["health"] == "unknown" and body["sync_status"] == "in_sync"
    d.report("unknown", stopped_by_user=True)
    assert d.detail()["stopped_by_user"] is True

    d.order("workload_start")
    [action] = d.collect()
    d.done(action["id"])
    assert d.detail()["stopped_by_user"] is False
    d.report()
    assert d.detail()["health"] == "healthy"

    listed = client.get(f"{d.url}/actions", headers=d.headers).json()
    assert [(a["kind"], a["state"]) for a in listed] == [("workload_start", "done"), ("workload_stop", "done")]
    assert listed[0]["requested_by"] == "owner@example.com"
    # A viewer may see what happened, not who.
    viewer = d.member(OrganizationRole.VIEWER)
    assert client.get(f"{d.url}/actions", headers=viewer).json()[0]["requested_by"] is None
    audit = db_session.scalars(select(AuditEvent.action).where(AuditEvent.action.like("software.%"))).all()
    assert sorted(audit) == ["software.start", "software.stop"]


def test_failed_stop_changes_nothing(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    d.order("workload_stop")
    [action] = d.collect()
    d.done(action["id"], "failed")
    body = d.detail()
    assert body["stopped_by_user"] is False and body["health"] == "healthy"


def test_works_without_a_broker(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    assert d.order("workload_restart").status_code == 200
    assert len(d.collect()) == 1


@pytest.mark.parametrize("kinds", [None, ["reboot"]])
def test_older_agents_are_refused(client: TestClient, db_session: Session, kinds: list[str] | None) -> None:
    d = _Device(client, db_session, kinds=kinds)
    assert d.detail()["supported_actions"] == []
    refused = d.order("workload_restart")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "action_unsupported"


def test_same_order_is_coalesced_and_orders_are_limited(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    first = d.order("workload_restart").json()
    assert d.order("workload_restart").json()["id"] == first["id"]
    assert len(d.collect()) == 1
    for _ in range(9):
        assert d.order("workload_restart").status_code == 200
        d.collect()
    limited = d.order("workload_restart")
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"
    # Refreshing is counted on its own.
    assert d.order("workload_snapshot").status_code == 200


def test_only_compose_has_services(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    assert d.order("workload_restart", service="web").status_code == 422
    assert d.order("workload_restart", service="--all").status_code == 422
    assert d.order("reboot").status_code == 422
    assert db_session.scalars(select(DeviceAction)).all() == []
    db_session.get(DeviceSoftware, uuid.UUID(d.id)).kind = "docker_compose"
    db_session.commit()
    assert d.order("workload_restart", service="web").status_code == 200
    assert d.collect()[0]["service"] == "web"


def test_action_the_device_never_reported_fails(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    d.order("workload_restart")
    d.collect()
    row = db_session.scalars(select(DeviceAction)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    [listed] = client.get(f"{d.url}/actions", headers=d.headers).json()
    assert listed["state"] == "failed" and "did not report" in listed["last_error"]


def test_a_stopped_workload_is_not_a_failed_rollout(client: TestClient, db_session: Session) -> None:
    from app.deployments.models import Deployment, DeploymentDevice
    from app.deployments.rollout import RolloutConfig
    from app.deployments.service import DeploymentService

    d = _Device(client, db_session)
    deployment = db_session.scalars(select(Deployment)).one()
    rows = list(db_session.scalars(select(DeploymentDevice)))
    config = RolloutConfig(phases=[{"percent": 100}], failure_threshold_percent=10)
    service = DeploymentService(db_session)
    d.report("unhealthy")
    assert service._rollout_rows(deployment, rows, config)[0].unhealthy is True
    d.report("unknown", stopped_by_user=True)
    db_session.expire_all()
    assert service._rollout_rows(deployment, rows, config)[0].unhealthy is False


# ------------------------------------------------------------------ history


@pytest.fixture
def history_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_plane_api_url", "http://data-api.test:8092")
    monkeypatch.setattr(get_settings(), "data_plane_internal_token", "test-internal")


def test_health_changes_are_written_to_the_outbox_once(
    client: TestClient, db_session: Session, history_on: None
) -> None:
    d = _Device(client, db_session)
    for _ in range(2):
        d.report("unhealthy", last_error="exit 1")
    for _ in range(2):
        d.report("unknown", stopped_by_user=True, last_error=None)
    rows = db_session.scalars(
        select(PlatformEventOutbox)
        .where(PlatformEventOutbox.stream == "meteor.software")
        .order_by(PlatformEventOutbox.created_at)
    ).all()
    assert [(r.data["from"], r.data["to"]) for r in rows] == [
        ("unknown", "healthy"),
        ("healthy", "unhealthy"),
        ("unhealthy", "stopped"),
    ]
    assert rows[1].data["workload_key"] == KEY and rows[1].data["reason"] == "exit 1"
    assert rows[0].organization_id == d.org.id and rows[0].type == "software.health.changed"


class _DataPlane:
    def __init__(self, events: dict[str, list[dict]]) -> None:
        self.events, self.names, self.orgs = events, "", set()

    def list_events(self, *, organization_id, params):
        self.orgs.add(organization_id)
        return {"events": self.events.get(params["stream"], [])}

    def get_device_metrics(self, *, organization_id, device_id, params):
        self.names = params["names"]
        point = {"t": params["start"], "avg": 1, "min": 1, "max": 1, "count": 1}
        return {
            "bucket_seconds": 900,
            "series": [{"name": "app.web.cpu", "points": [point]}, {"name": "app.web.mem", "points": []}],
        }


def test_history(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    containers = [
        {"name": "web", "metric": "web", "state": "running"},
        {"name": "long", "metric": "long~1", "state": "running"},
    ]
    client.post("/api/v1/agent/software/snapshots", headers=d.agent, json=_snapshot(containers=containers))
    now = datetime.now(UTC)

    def at(hours: float) -> str:
        return (now - timedelta(hours=hours)).isoformat()

    def change(hours: float, before: str, after: str, key: str = KEY) -> dict:
        return {"time": at(hours), "data": {"workload_key": key, "from": before, "to": after, "reason": "exit 1"}}

    plane = _DataPlane(
        {
            "meteor.software": [
                change(12, "healthy", "unhealthy"),
                change(11, "healthy", "unhealthy", "other"),
                change(6, "unhealthy", "healthy"),
            ],
            "meteor.device.events": [
                {"time": at(1), "type": "app.exited", "data": {"app": "web", "exit_code": 137}},
                {"time": at(2), "type": "app.exited", "data": {"app": "gone", "workload_key": KEY}},
                {"time": at(3), "type": "app.exited", "data": {"app": "web", "workload_key": "other"}},
                {"time": at(4), "type": "app.exited", "data": {"app": "customer"}},
            ],
            "meteor.deployments": [
                {"time": at(5), "data": {"status": "succeeded", "release_id": d.release["id"]}},
                {"time": at(5), "data": {"status": "succeeded", "release_id": str(uuid.uuid4())}},
            ],
        }
    )
    client.app.dependency_overrides[get_data_plane_api] = lambda: plane
    try:
        response = client.get(f"{d.url}/history?range=24h", headers=d.headers)
    finally:
        client.app.dependency_overrides.pop(get_data_plane_api, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [s["status"] for s in body["segments"]] == ["healthy", "unhealthy", "healthy"]
    assert body["healthy_percent"] == pytest.approx(75, abs=0.1)
    assert [e["app"] for e in body["events"]] == ["web", "gone"]
    assert len(body["deployments"]) == 1
    # The data plane takes no "~" in metric names.
    assert plane.names == "app.web.cpu,app.web.mem"
    assert [s["name"] for s in body["series"]] == ["web.cpu"]
    assert plane.orgs == {d.org.id}


def test_history_needs_the_data_plane(client: TestClient, db_session: Session) -> None:
    d = _Device(client, db_session)
    client.app.dependency_overrides[get_data_plane_api] = lambda: None
    try:
        assert client.get(f"{d.url}/history", headers=d.headers).status_code == 503
    finally:
        client.app.dependency_overrides.pop(get_data_plane_api, None)


def test_segments_cover_the_window() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)
    middle = start + timedelta(hours=1)
    segments = health_segments(start, end, "healthy", [HealthChange(t=middle, status="stopped")])
    assert [(s.start, s.end, s.status) for s in segments] == [(start, middle, "healthy"), (middle, end, "stopped")]


def test_device_group_scope(client: TestClient, db_session: Session) -> None:
    from app.authorization.service import AuthzService
    from app.devices.models import Device, DeviceGroup

    d = _Device(client, db_session)
    mine = DeviceGroup(organization_id=d.org.id, name="Mine", slug="mine", labels={}, metadata_={})
    other = DeviceGroup(organization_id=d.org.id, name="Other", slug="other", labels={}, metadata_={})
    db_session.add_all([mine, other])
    db_session.commit()
    user = create_user(db_session, email="scoped@example.com")
    membership = add_member(db_session, d.org, user, OrganizationRole.OPERATOR)
    AuthzService(db_session).replace_device_group_scope(membership, [mine.id])
    db_session.commit()
    headers = auth_header(client, "scoped@example.com")
    device = db_session.get(Device, uuid.UUID(d.reg["device_id"]))
    device.device_group_id = other.id
    db_session.commit()
    assert d.order("workload_stop", headers).status_code == 403
    assert client.get(d.url, headers=headers).status_code == 403
    device.device_group_id = mine.id
    db_session.commit()
    assert d.order("workload_stop", headers).status_code == 200
