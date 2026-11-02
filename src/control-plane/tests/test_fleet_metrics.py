"""Fleet gauges computed on scrape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.devices.fleet_metrics import FleetMetrics
from app.devices.models import Device
from app.main import create_app
from tests.conftest import create_org_with_owner, create_user
from tests.metrics_helpers import sample


def _device(session: Session, org, name: str, *, last_seen: datetime | None, enabled: bool = True) -> Device:
    device = Device(organization_id=org.id, name=name, last_seen_at=last_seen, is_enabled=enabled)
    session.add(device)
    session.commit()
    return device


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_fleet_snapshot_counts_states(db_session: Session, engine) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    now = datetime.now(UTC)
    _device(db_session, org, "online-1", last_seen=now)
    _device(db_session, org, "online-2", last_seen=now - timedelta(seconds=10))
    _device(db_session, org, "offline", last_seen=now - timedelta(hours=1))
    _device(db_session, org, "never", last_seen=None, enabled=False)

    fleet = FleetMetrics(sessionmaker(bind=engine), offline_threshold_seconds=150)
    snapshot = fleet.snapshot()

    states = {labels["state"]: value for labels, value in snapshot["meteorcloud_devices"]}
    assert states == {"online": 2, "offline": 1, "never_seen": 1}
    assert snapshot["meteorcloud_devices_disabled"] == [({}, 1)]
    assert snapshot["meteorcloud_deployments"] == [({"status": "in_progress"}, 0)]


def test_fleet_snapshot_is_cached(db_session: Session, engine) -> None:
    calls = []
    factory = sessionmaker(bind=engine)

    def counting_factory():
        calls.append(1)
        return factory()

    clock = _Clock()
    fleet = FleetMetrics(counting_factory, offline_threshold_seconds=150, cache_seconds=30, clock=clock)
    fleet.snapshot()
    clock.now += 10
    fleet.snapshot()
    assert len(calls) == 1
    clock.now += 30
    fleet.snapshot()
    assert len(calls) == 2


def test_database_error_yields_no_samples_and_scrape_succeeds() -> None:
    def broken():
        raise RuntimeError("database down")

    fleet = FleetMetrics(broken, offline_threshold_seconds=150)
    assert fleet.snapshot() == {}


def test_fleet_gauges_on_metrics_endpoint(client: TestClient, db_session: Session) -> None:
    owner = create_user(db_session, email="owner@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    _device(db_session, org, "edge", last_seen=datetime.now(UTC))

    body = client.get("/metrics").text

    assert 'meteorcloud_devices{state="online"}' in body
    assert sample("meteorcloud_build_info", version=get_settings().app_version) == 1


@pytest.mark.parametrize("env", ["METRICS_ENABLED", "FLEET_METRICS_ENABLED"])
def test_switches_skip_fleet_queries(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    calls = []
    monkeypatch.setattr("app.devices.fleet_metrics.FleetMetrics.register", lambda self: calls.append(1))
    monkeypatch.setenv(env, "false")
    get_settings.cache_clear()
    try:
        create_app()
    finally:
        monkeypatch.delenv(env)
        get_settings.cache_clear()
    assert calls == []
    create_app()
    assert calls == [1]
