"""Pure rollout engine: phase sizing, failure rate, rollout validation, tick planning."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.deployments.rollout import (
    RolloutConfig,
    RolloutRow,
    batched_as_phases,
    failure_rate,
    phase_sizes,
    plan_tick,
)
from app.deployments.schemas import DeployRollout, RolloutPhase

CANARY_10_100 = [{"count": 1}, {"percent": 10, "delay_seconds": 3600}, {"percent": 100, "delay_seconds": 86400}]
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("total", "expected"),
    [(1, [1, 1, 1]), (7, [1, 1, 7]), (50, [1, 5, 50]), (1000, [1, 100, 1000])],
)
def test_phase_sizes_canary_ten_percent_all(total: int, expected: list[int]) -> None:
    assert phase_sizes(total, CANARY_10_100) == expected


def test_phase_sizes_round_up_with_minimum_one() -> None:
    assert phase_sizes(3, [{"percent": 1}, {"percent": 34}, {"percent": 100}]) == [1, 2, 3]
    assert phase_sizes(21, [{"percent": 10}, {"percent": 100}]) == [3, 21]
    assert phase_sizes(20, [{"percent": 10}, {"percent": 100}]) == [2, 20]


def test_phase_sizes_clamp_to_total_and_never_shrink() -> None:
    assert phase_sizes(3, [{"count": 5}, {"percent": 100}]) == [3, 3]
    assert phase_sizes(20, [{"count": 5}, {"percent": 10}, {"percent": 100}]) == [5, 5, 20]


def test_phase_sizes_accept_models() -> None:
    phases = [RolloutPhase(count=2), RolloutPhase(percent=100, delay_seconds=60)]
    assert phase_sizes(10, phases) == [2, 10]


def test_batched_as_phases_matches_batches() -> None:
    phases = batched_as_phases(5, 2, 600)
    assert phases == [{"count": 2}, {"count": 4, "delay_seconds": 600}, {"percent": 100, "delay_seconds": 600}]
    assert phase_sizes(5, phases) == [2, 4, 5]
    assert batched_as_phases(2, 5, None) == [{"percent": 100}]


def test_failure_rate() -> None:
    assert failure_rate(0, 0) == 0.0
    assert failure_rate(9, 1) == pytest.approx(0.1)
    assert failure_rate(0, 3) == 1.0


def _phased(**overrides) -> DeployRollout:
    body = {"strategy": "phased", "phases": CANARY_10_100, **overrides}
    return DeployRollout.model_validate(body)


def test_phased_rollout_accepts_reference_body() -> None:
    rollout = _phased(max_concurrent=50, failure_threshold_percent=10, pending_timeout_seconds=86400)
    record = rollout.to_record()
    assert record["strategy"] == "phased"
    assert record["phases"][1] == {"count": None, "percent": 10, "delay_seconds": 3600}
    assert record["max_concurrent"] == 50


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"strategy": "phased"}, "requires phases"),
        ({"strategy": "phased", "phases": []}, "at least 1"),
        ({"strategy": "phased", "phases": [{"percent": 100}] * 11}, "at most 10"),
        ({"strategy": "phased", "phases": [{"count": 1, "percent": 5}, {"percent": 100}]}, "exactly one"),
        ({"strategy": "phased", "phases": [{}, {"percent": 100}]}, "exactly one"),
        ({"strategy": "phased", "phases": [{"count": 1}, {"percent": 50}]}, "last phase must be percent 100"),
        ({"strategy": "phased", "phases": [{"count": 1, "delay_seconds": 5}, {"percent": 100}]}, "first phase"),
        ({"strategy": "phased", "phases": [{"count": 5}, {"count": 5}, {"percent": 100}]}, "strictly increasing"),
        ({"strategy": "phased", "phases": [{"percent": 50}, {"percent": 20}, {"percent": 100}]}, "strictly"),
        ({"strategy": "phased", "phases": [{"count": 1}, {"percent": 100, "delay_seconds": 604801}]}, "604800"),
        ({"strategy": "phased", "phases": [{"percent": 100}], "pending_timeout_seconds": 30}, "60"),
        ({"strategy": "phased", "phases": [{"percent": 100}], "failure_threshold_percent": 101}, "100"),
        ({"strategy": "phased", "phases": [{"percent": 100}], "max_concurrent": 0}, "1"),
        ({"strategy": "phased", "phases": [{"percent": 100}], "batch_size": 2}, "batch_size"),
        ({"strategy": "batched", "batch_size": 2, "max_concurrent": 5}, "only supported with strategy 'phased'"),
        ({"strategy": "immediate", "phases": [{"percent": 100}]}, "only supported with strategy 'phased'"),
        ({"strategy": "rolling"}, "'immediate', 'batched' or 'phased'"),
    ],
)
def test_rollout_validation_errors(body: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        DeployRollout.model_validate(body)


def test_immediate_and_batched_records_keep_their_shape() -> None:
    assert DeployRollout().to_record() == {"strategy": "immediate", "batch_size": None, "delay_seconds": None}
    assert DeployRollout(strategy="batched", batch_size=2).to_record() == {
        "strategy": "batched",
        "batch_size": 2,
        "delay_seconds": None,
    }


def _rows(count: int, status: str = "queued") -> list[RolloutRow]:
    return [RolloutRow(device_id=uuid.UUID(int=index + 1), status=status) for index in range(count)]


def test_plan_tick_first_wave_then_waits() -> None:
    config = RolloutConfig(phases=CANARY_10_100)
    rows = _rows(20)
    plan = plan_tick(rows, {}, config, NOW)
    assert plan.ids_to_activate == [rows[0].device_id]
    assert plan.new_state["current_phase"] == 0

    activated = [RolloutRow(device_id=rows[0].device_id, status="pending", activated_at=NOW), *rows[1:]]
    again = plan_tick(activated, plan.new_state, config, NOW)
    assert again.ids_to_activate == []


def test_plan_tick_skips_phases_that_add_no_devices() -> None:
    config = RolloutConfig(phases=CANARY_10_100)
    rows = [RolloutRow(device_id=uuid.UUID(int=1), status="succeeded", activated_at=NOW), *_rows(6)[1:]]
    state = {"current_phase": 0, "phase_completed_at": (NOW - timedelta(hours=2)).isoformat()}
    # 7 devices: 10% is still 1, so phase 2 is skipped and phase 3's 24 h delay applies.
    assert plan_tick(rows, state, config, NOW).ids_to_activate == []
    state["phase_completed_at"] = (NOW - timedelta(hours=25)).isoformat()
    plan = plan_tick(rows, state, config, NOW)
    assert len(plan.ids_to_activate) == 5
    assert plan.new_state["current_phase"] == 2


def test_plan_tick_zero_threshold_pauses_on_first_failure() -> None:
    config = RolloutConfig(phases=[{"count": 2}, {"percent": 100}], failure_threshold_percent=0)
    rows = [
        RolloutRow(device_id=uuid.UUID(int=1), status="succeeded", activated_at=NOW),
        RolloutRow(device_id=uuid.UUID(int=2), status="pending", activated_at=NOW),
        *_rows(4)[2:],
    ]
    assert plan_tick(rows, {"current_phase": 0}, config, NOW).pause_reason is None
    rows[1] = RolloutRow(device_id=uuid.UUID(int=2), status="failed", activated_at=NOW)
    assert plan_tick(rows, {"current_phase": 0}, config, NOW).pause_reason == "failure_threshold"
