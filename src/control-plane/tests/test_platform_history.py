"""Platform history: deployment outbox, relay to the data plane, and the history endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.dependencies import get_data_plane_api
from app.authorization.service import AuthzService
from app.core.config import get_settings
from app.devices.history import build_segments, parse_app_readings
from app.devices.models import DeviceGroup
from app.history.models import PlatformEventOutbox
from app.history.relay import relay_batch
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, auth_header, create_user
from tests.test_deployments import _register_device, _setup_release


@pytest.fixture
def history_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_plane_api_url", "http://data-api.test:8092")
    monkeypatch.setattr(get_settings(), "data_plane_internal_token", "test-internal")


class _FakeDataPlane:
    def __init__(self, events: dict[str, dict] | None = None, fail: bool = False) -> None:
        self.events = events or {}
        self.fail = fail
        self.written: list[dict] = []
        self.calls: list[dict] = []

    def write_events(self, events):
        if self.fail:
            raise RuntimeError("data plane down")
        self.written.extend(events)
        return {"received": len(events), "inserted": len(events)}

    def list_events(self, *, organization_id, params):
        self.calls.append({"organization_id": organization_id, **params})
        return self.events.get(params["stream"], {"events": [], "previous": None})

    def online_devices(self, *, organization_id, params):
        self.calls.append({"organization_id": organization_id, **params})
        return {
            "start": params["start"],
            "end": params["end"],
            "bucket_seconds": 900,
            "buckets": [{"t": params["start"], "devices": 3}],
        }


def _outbox(db_session: Session) -> list[PlatformEventOutbox]:
    return list(db_session.scalars(select(PlatformEventOutbox).order_by(PlatformEventOutbox.created_at)))


def _deploy_and_succeed(client: TestClient, db_session: Session):
    org, headers, token, release = _setup_release(client, db_session)
    reg = _register_device(client, token, name="edge-01", mac="aa:bb:cc:dd:ee:51")
    created = client.post(
        f"/api/v1/organizations/{org.id}/deployments",
        headers=headers,
        json={"release_id": release["id"], "target": {"device_ids": [reg["device_id"]]}},
    )
    assert created.status_code in (200, 201), created.text
    heartbeat = client.post(
        "/api/v1/agent/heartbeat",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        json={
            "software_report": [
                {
                    "workload_key": "camera-agent.service",
                    "installed_release_id": release["id"],
                    "installed_version": release["version"],
                    "health": "healthy",
                    "sync_status": "in_sync",
                    "progress_percent": 100,
                }
            ]
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    return org, headers, reg, release, created.json()


def test_deployment_status_changes_are_written_to_the_outbox(
    client: TestClient, db_session: Session, history_on: None
) -> None:
    org, _, reg, release, deployment = _deploy_and_succeed(client, db_session)
    rows = _outbox(db_session)
    statuses = [row.data["status"] for row in rows]
    assert statuses[0] == "pending" and statuses[-1] == "succeeded", statuses
    last = rows[-1]
    assert last.stream == "meteor.deployments" and last.type == "deployment.device.succeeded"
    assert last.organization_id == org.id and str(last.device_id) == reg["device_id"]
    assert last.data["deployment_id"] == deployment["id"] and last.data["release_id"] == release["id"]
    assert last.data["previous_status"] == statuses[-2]
    assert rows[0].data["previous_status"] is None


def test_no_outbox_without_a_data_plane(client: TestClient, db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "data_plane_internal_token", "")
    _deploy_and_succeed(client, db_session)
    assert _outbox(db_session) == []


def _row(org_id: uuid.UUID, created_at: datetime | None = None) -> PlatformEventOutbox:
    row = PlatformEventOutbox(
        id=uuid.uuid4(),
        organization_id=org_id,
        device_id=uuid.uuid4(),
        stream="meteor.deployments",
        type="deployment.device.failed",
        occurred_at=datetime.now(UTC),
        data={"status": "failed"},
    )
    if created_at is not None:
        row.created_at = created_at
    return row


def test_relay_sends_then_deletes_and_keeps_rows_on_failure(db_session: Session) -> None:
    org_id = uuid.uuid4()
    rows = [_row(org_id), _row(org_id)]
    db_session.add_all(rows)
    db_session.commit()

    down = _FakeDataPlane(fail=True)
    with pytest.raises(RuntimeError):
        relay_batch(db_session, down)
    assert len(_outbox(db_session)) == 2, "nothing may be deleted when the data plane fails"

    up = _FakeDataPlane()
    assert relay_batch(db_session, up) == 2
    assert _outbox(db_session) == []
    assert {e["id"] for e in up.written} == {str(r.id) for r in rows}
    assert up.written[0]["stream"] == "meteor.deployments" and up.written[0]["data"] == {"status": "failed"}
    assert relay_batch(db_session, up) == 0


def test_relay_drops_events_older_than_a_week(db_session: Session) -> None:
    org_id = uuid.uuid4()
    db_session.add_all([_row(org_id, datetime.now(UTC) - timedelta(days=8)), _row(org_id)])
    db_session.commit()
    up = _FakeDataPlane()
    assert relay_batch(db_session, up) == 1
    assert _outbox(db_session) == []


def test_build_segments_and_app_readings() -> None:
    start = datetime(2026, 11, 15, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=4)
    segments = build_segments(
        start,
        end,
        "online",
        [(start + timedelta(hours=1), "offline"), (start + timedelta(hours=2), "online"), (end, "online")],
    )
    assert [(s.status, s.end - s.start) for s in segments] == [
        ("online", timedelta(hours=1)),
        ("offline", timedelta(hours=1)),
        ("online", timedelta(hours=2)),
    ]
    assert build_segments(start, end, None, [])[0].status == "unknown"

    apps = parse_app_readings(
        {"cpu_percent": 5, "app.web.cpu": 12.5, "app.web.healthy": 1, "app.web.up": 1, "app.cam.v2.restarts": 3}
    )
    assert [a.name for a in apps] == ["cam.v2", "web"]
    assert apps[0].restarts == 3 and apps[1].cpu_percent == 12.5 and apps[1].healthy and apps[1].running


def test_history_endpoints(client: TestClient, db_session: Session, history_on: None) -> None:
    org, headers, reg, release, deployment = _deploy_and_succeed(client, db_session)
    device_id = reg["device_id"]
    now = datetime.now(UTC)
    fake = _FakeDataPlane(
        {
            "meteor.device.status": {
                "previous": {"time": (now - timedelta(days=2)).isoformat(), "data": {"status": "online"}},
                "events": [
                    {"time": (now - timedelta(hours=2)).isoformat(), "data": {"status": "offline"}},
                    {"time": (now - timedelta(hours=1)).isoformat(), "data": {"status": "online"}},
                ],
            },
            "meteor.deployments": {
                "events": [
                    {
                        "time": now.isoformat(),
                        "type": "deployment.device.succeeded",
                        "data": {
                            "status": "succeeded",
                            "previous_status": "installing",
                            "deployment_id": deployment["id"],
                            "release_id": release["id"],
                            "seconds": 42.5,
                        },
                    }
                ]
            },
            "meteor.device.metrics": {"events": [{"time": now.isoformat(), "data": {"app.web.cpu": 3.5}}]},
            "meteor.device.events": {
                "events": [
                    {
                        "time": now.isoformat(),
                        "type": "app.exited",
                        "data": {"app": "web", "exit_code": 137, "oom_killed": True},
                    },
                    {"time": now.isoformat(), "type": "agent.started", "data": {}},
                ]
            },
        }
    )
    client.app.dependency_overrides[get_data_plane_api] = lambda: fake
    base = f"/api/v1/organizations/{org.id}"

    availability = client.get(f"{base}/devices/{device_id}/availability", headers=headers)
    assert availability.status_code == 200, availability.text
    body = availability.json()
    assert body["offline_count"] == 1
    assert 95 < body["uptime_percent"] < 96  # one hour offline out of 24
    assert [s["status"] for s in body["segments"]] == ["online", "offline", "online"]
    status_call = next(c for c in fake.calls if c.get("stream") == "meteor.device.status")
    assert status_call["previous"] == "1" and status_call["device_id"] == device_id

    history = client.get(f"{base}/devices/{device_id}/deployment-history", headers=headers)
    assert history.status_code == 200, history.text
    item = history.json()["items"][0]
    assert item["status"] == "succeeded" and item["seconds"] == 42.5
    assert item["release_name"] == "camera-agent" and item["release_version"] == "1.2.0"

    apps = client.get(f"{base}/devices/{device_id}/apps", headers=headers)
    assert apps.status_code == 200, apps.text
    assert apps.json()["apps"] == [
        {
            "name": "web",
            "cpu_percent": 3.5,
            "memory_bytes": None,
            "restarts": None,
            "healthy": None,
            "health_ms": None,
            "running": None,
        }
    ]
    events = apps.json()["events"]
    assert len(events) == 1 and events[0]["exit_code"] == 137 and events[0]["oom_killed"] is True

    online = client.get(f"{base}/fleet/online-history?range=7d", headers=headers)
    assert online.status_code == 200, online.text
    assert online.json()["total_devices"] == 1 and online.json()["buckets"][0]["devices"] == 3
    assert fake.calls[-1]["bucket"] == "1h"

    # A device-group-scoped member may not see the organization-wide count.
    operator = create_user(db_session, email="ops@example.com")
    membership = add_member(db_session, org, operator, OrganizationRole.OPERATOR)
    berlin = DeviceGroup(organization_id=org.id, name="Berlin", slug="berlin", labels={}, metadata_={})
    db_session.add(berlin)
    db_session.flush()
    AuthzService(db_session).replace_device_group_scope(membership, [berlin.id])
    db_session.commit()
    scoped = client.get(f"{base}/fleet/online-history", headers=auth_header(client, "ops@example.com"))
    assert scoped.status_code == 403
    assert scoped.json()["error"]["code"] == "fleet_scope_required"

    client.app.dependency_overrides[get_data_plane_api] = lambda: None
    assert client.get(f"{base}/devices/{device_id}/availability", headers=headers).status_code == 503


def test_log_history_needs_device_logs_and_searches(client: TestClient, db_session: Session) -> None:
    org, headers, reg, _, _ = _deploy_and_succeed(client, db_session)
    now = datetime.now(UTC)
    fake = _FakeDataPlane(
        {
            "meteor.device.logs": {
                "events": [
                    {
                        "time": now.isoformat(),
                        "data": {"type": "log", "message": "disk failing", "priority": 3, "unit": "smartd.service"},
                    },
                    {"time": now.isoformat(), "data": {"type": "log"}},
                ],
                "next_cursor": "abc",
            }
        }
    )
    client.app.dependency_overrides[get_data_plane_api] = lambda: fake
    url = f"/api/v1/organizations/{org.id}/devices/{reg['device_id']}/log-history"

    response = client.get(url + "?q=%20disk%20&range=30d&cursor=xyz", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entries"] == [
        {
            "t": body["entries"][0]["t"],
            "message": "disk failing",
            "priority": 3,
            "unit": "smartd.service",
            "source": None,
        }
    ]
    assert body["next_cursor"] == "abc" and body["query"] == "disk"
    call = fake.calls[-1]
    assert call["stream"] == "meteor.device.logs" and call["q"] == "disk" and call["cursor"] == "xyz"

    # A viewer has device.read but not device.logs.
    viewer = create_user(db_session, email="viewer@example.com")
    add_member(db_session, org, viewer, OrganizationRole.VIEWER)
    calls = len(fake.calls)
    assert client.get(url, headers=auth_header(client, "viewer@example.com")).status_code == 403
    assert len(fake.calls) == calls


class _PagedStatus:
    """Status history served in pages, like the data plane does past its limit."""

    def __init__(self, pages: list[list[dict]], previous: dict | None) -> None:
        self.pages = pages
        self.previous = previous
        self.calls: list[dict] = []

    def list_events(self, *, organization_id, params):
        self.calls.append(dict(params))
        index = int(params.get("cursor") or 0)
        return {
            "events": self.pages[index],
            "previous": self.previous if index == 0 else None,
            "next_cursor": str(index + 1) if index + 1 < len(self.pages) else None,
        }


def test_availability_reads_every_page_and_counts_transitions() -> None:
    from app.devices.history import device_availability

    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

    def ev(hours_ago: float, status: str) -> dict:
        return {"time": (now - timedelta(hours=hours_ago)).isoformat(), "data": {"status": status}}

    dp = _PagedStatus(
        [
            [ev(20, "offline"), ev(20, "offline"), ev(19, "online")],
            # The second page holds a later outage; it must not be lost.
            [ev(5, "offline"), ev(4, "online")],
        ],
        previous={"time": (now - timedelta(days=2)).isoformat(), "data": {"status": "online"}},
    )
    body = device_availability(dp, organization_id=uuid.uuid4(), device_id=uuid.uuid4(), range_="24h", now=now)
    assert len(dp.calls) == 2 and dp.calls[1]["cursor"] == "1"
    assert body.offline_count == 2, "a repeated offline message is the same outage"
    assert [s.status for s in body.segments] == ["online", "offline", "online", "offline", "online"]
    assert body.uptime_percent == round(22 * 100 / 24, 2)


def test_equal_timestamps_keep_the_received_order() -> None:
    start = datetime(2026, 11, 15, 0, 0, tzinfo=UTC)
    at = start + timedelta(hours=1)
    segments = build_segments(start, start + timedelta(hours=2), "offline", [(at, "online"), (at, "offline")])
    assert segments[-1].status == "offline"


def test_lwt_offline_right_after_online_is_ignored() -> None:
    from app.devices.history import debounce_status_changes, device_availability

    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    online_at = now - timedelta(minutes=30)
    lwt_at = online_at + timedelta(milliseconds=400)

    def ev(at: datetime, status: str) -> dict:
        return {"time": at.isoformat(), "data": {"status": status}}

    assert debounce_status_changes([(online_at, "online"), (lwt_at, "offline")]) == [
        (online_at, "online")
    ]

    dp = _PagedStatus([[ev(online_at, "online"), ev(lwt_at, "offline")]], previous=None)
    body = device_availability(
        dp, organization_id=uuid.uuid4(), device_id=uuid.uuid4(), range_="1h", now=now
    )
    assert body.offline_count == 0
    assert body.segments[-1].status == "online"
    # Unknown time before the first status does not count toward uptime.
    assert body.uptime_percent == 100.0
    assert [s.status for s in body.segments] == ["unknown", "online"]


def test_debounce_uses_latest_online_for_race_window() -> None:
    from app.devices.history import debounce_status_changes

    first = datetime(2026, 11, 15, 11, 0, tzinfo=UTC)
    reconfirm = first + timedelta(minutes=20)
    lwt = reconfirm + timedelta(milliseconds=400)
    assert debounce_status_changes(
        [(first, "online"), (reconfirm, "online"), (lwt, "offline")]
    ) == [(first, "online")]


def test_live_online_splits_from_last_seen_not_whole_interval() -> None:
    from app.devices.history import device_availability

    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    offline_at = now - timedelta(minutes=20)
    last_seen = now - timedelta(minutes=1)

    dp = _PagedStatus(
        [[{"time": offline_at.isoformat(), "data": {"status": "offline"}}]],
        previous={"time": (now - timedelta(hours=2)).isoformat(), "data": {"status": "online"}},
    )
    body = device_availability(
        dp,
        organization_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        range_="1h",
        now=now,
        live_status="online",
        last_seen_at=last_seen,
    )
    assert [s.status for s in body.segments] == ["online", "offline", "online"]
    assert body.segments[-1].start == last_seen
    assert body.offline_count == 1


def test_live_online_without_last_seen_leaves_history() -> None:
    from app.devices.history import device_availability

    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    dp = _PagedStatus([[]], previous=None)
    body = device_availability(
        dp,
        organization_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        range_="1h",
        now=now,
        live_status="online",
    )
    assert body.uptime_percent is None
    assert [s.status for s in body.segments] == ["unknown"]


def test_relay_pass_is_bounded(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.history import relay

    calls = []

    def fake_batch(session, data_plane, *, now=None):
        calls.append(1)
        return relay.BATCH_SIZE  # a backlog that never runs dry

    monkeypatch.setattr(relay, "relay_batch", fake_batch)
    engine = db_session.get_bind()
    sent = relay.relay_once(engine, lambda: Session(bind=engine), _FakeDataPlane())
    assert len(calls) == relay.MAX_BATCHES_PER_PASS
    assert sent == relay.BATCH_SIZE * relay.MAX_BATCHES_PER_PASS
