"""Progressive rollout engine: pure planning, no database access.

``plan_tick`` looks at a Deploy's per-device rows and its ``rollout.state`` and
decides what to change on this tick: which devices to activate, which to mark
timed out, and whether to pause. The service applies the plan under a row lock.

Batched Deploys run on the same engine through ``batched_as_phases``; their soak
delay counts from when a batch was released (as before), phased Deploys count it
from when the previous phase became terminal.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

PENDING = "pending"
QUEUED = "queued"
UPDATING = "updating"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
SKIPPED = "skipped"
TIMED_OUT = "timed_out"

OPEN_STATUSES = frozenset({PENDING, UPDATING})
TERMINAL_STATUSES = frozenset({SUCCEEDED, FAILED, TIMED_OUT, CANCELLED})

PAUSE_FAILURE_THRESHOLD = "failure_threshold"
PAUSE_MANUAL = "manual"


@dataclass(frozen=True)
class RolloutRow:
    device_id: uuid.UUID
    status: str
    activated_at: datetime | None = None
    last_seen_at: datetime | None = None
    # Current health from device_software: a succeeded device that is now unhealthy counts as failed.
    unhealthy: bool = False


@dataclass(frozen=True)
class RolloutConfig:
    phases: Sequence[Mapping[str, Any]]
    max_concurrent: int | None = None
    failure_threshold_percent: int | None = None
    pending_timeout_seconds: int | None = None
    # Batched: delay from when the batch was released. Phased: from when the phase became terminal.
    delay_from_phase_start: bool = False
    # Phased picks the most recently seen devices first; batched keeps target order.
    order_by_last_seen: bool = True


@dataclass
class TickPlan:
    ids_to_activate: list[uuid.UUID] = field(default_factory=list)
    ids_to_time_out: list[uuid.UUID] = field(default_factory=list)
    pause_reason: str | None = None
    new_state: dict[str, Any] = field(default_factory=dict)


def _phase_value(phase: Any, key: str) -> Any:
    if isinstance(phase, Mapping):
        return phase.get(key)
    return getattr(phase, key, None)


def phase_sizes(total: int, phases: Sequence[Any]) -> list[int]:
    """Cumulative device count at the end of each phase (percent rounds up, min 1)."""
    sizes: list[int] = []
    previous = 0
    for phase in phases:
        count = _phase_value(phase, "count")
        if count is not None:
            raw = int(count)
        else:
            percent = int(_phase_value(phase, "percent") or 0)
            raw = max(1, -(-total * percent // 100))
        size = min(total, max(raw, previous))
        sizes.append(size)
        previous = size
    return sizes


def batched_as_phases(total: int, batch_size: int, delay_seconds: int | None) -> list[dict[str, Any]]:
    """Fixed-size batches expressed as phases (no failure threshold)."""
    batch_size = max(1, int(batch_size))
    phases: list[dict[str, Any]] = []
    size = batch_size
    while size < total:
        phase: dict[str, Any] = {"count": size}
        if phases and delay_seconds:
            phase["delay_seconds"] = delay_seconds
        phases.append(phase)
        size += batch_size
    last: dict[str, Any] = {"percent": 100}
    if phases and delay_seconds:
        last["delay_seconds"] = delay_seconds
    phases.append(last)
    return phases


def failure_rate(succeeded: int, failed: int) -> float:
    """failed / (succeeded + failed); 0.0 when nothing finished yet."""
    finished = succeeded + failed
    if finished <= 0:
        return 0.0
    return failed / finished


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _current_phase(state: Mapping[str, Any], sizes: list[int], activated: int) -> int | None:
    raw = state.get("current_phase")
    if isinstance(raw, int) and 0 <= raw < len(sizes):
        return raw
    if activated == 0:
        return None
    # Deploys started before rollout.state existed: the first phase that covers them.
    for index, size in enumerate(sizes):
        if size >= activated:
            return index
    return len(sizes) - 1


def plan_tick(
    rows: Sequence[RolloutRow],
    state: Mapping[str, Any] | None,
    config: RolloutConfig,
    now: datetime,
) -> TickPlan:
    """Decide timeouts, pause and activations for one tick of an in-progress Deploy."""
    new_state: dict[str, Any] = dict(state or {})
    plan = TickPlan(new_state=new_state)
    deployable = [row for row in rows if row.status != SKIPPED]
    total = len(deployable)
    if total == 0 or not config.phases:
        return plan

    statuses = {row.device_id: row.status for row in deployable}
    timeout = config.pending_timeout_seconds
    if timeout:
        for row in deployable:
            if (
                row.status == PENDING
                and row.activated_at is not None
                and _parse_time(row.activated_at) + timedelta(seconds=timeout) <= now
            ):
                plan.ids_to_time_out.append(row.device_id)
                statuses[row.device_id] = TIMED_OUT

    activated = [row for row in deployable if row.activated_at is not None]
    candidates = [row for row in deployable if row.activated_at is None and row.status == QUEUED]
    if config.order_by_last_seen:
        # Most recently seen first, never seen last; sorted() keeps target order within ties.
        seen = sorted(
            (row for row in candidates if row.last_seen_at is not None),
            key=lambda row: _parse_time(row.last_seen_at),
            reverse=True,
        )
        candidates = seen + [row for row in candidates if row.last_seen_at is None]

    succeeded = failed = 0
    for row in activated:
        status = statuses[row.device_id]
        if status == FAILED or (status == SUCCEEDED and row.unhealthy):
            failed += 1
        elif status == SUCCEEDED:
            succeeded += 1

    threshold = config.failure_threshold_percent
    # Pausing only matters while devices are still waiting to be activated.
    if (
        threshold is not None
        and candidates
        and failed > 0
        and failed * 100 >= threshold * (succeeded + failed)
    ):
        plan.pause_reason = PAUSE_FAILURE_THRESHOLD
        new_state["paused_reason"] = PAUSE_FAILURE_THRESHOLD
        new_state["paused_at"] = now.isoformat()
        return plan

    sizes = phase_sizes(total, config.phases)
    phase = _current_phase(new_state, sizes, len(activated))
    if phase is None:
        phase = 0
        new_state["current_phase"] = 0
        new_state["phase_started_at"] = now.isoformat()
        new_state["phase_completed_at"] = None
    elif "current_phase" not in new_state:
        new_state["current_phase"] = phase

    target = sizes[phase]
    if len(activated) >= target:
        if any(statuses[row.device_id] not in TERMINAL_STATUSES for row in activated):
            new_state["phase_completed_at"] = None
            return plan
        completed_at = _parse_time(new_state.get("phase_completed_at"))
        if completed_at is None:
            completed_at = now
            new_state["phase_completed_at"] = now.isoformat()
        # Phases that add no devices for this fleet size are skipped with their delay.
        next_phase = next(
            (index for index in range(phase + 1, len(sizes)) if sizes[index] > len(activated)),
            None,
        )
        if next_phase is None or not candidates:
            return plan
        delay = int(_phase_value(config.phases[next_phase], "delay_seconds") or 0)
        anchor = (
            _parse_time(new_state.get("phase_started_at")) if config.delay_from_phase_start else completed_at
        )
        if delay and anchor is not None and now < anchor + timedelta(seconds=delay):
            return plan
        phase = next_phase
        target = sizes[phase]
        new_state["current_phase"] = phase
        new_state["phase_started_at"] = now.isoformat()
        new_state["phase_completed_at"] = None

    wanted = target - len(activated)
    if config.max_concurrent is not None:
        in_flight = sum(1 for row in activated if statuses[row.device_id] in OPEN_STATUSES)
        wanted = min(wanted, config.max_concurrent - in_flight)
    if wanted > 0:
        plan.ids_to_activate = [row.device_id for row in candidates[:wanted]]
    return plan


def config_from_rollout(rollout: Mapping[str, Any], total: int) -> RolloutConfig | None:
    """Engine config for a stored rollout; None for strategies the engine does not drive."""
    strategy = rollout.get("strategy")
    if strategy == "batched":
        return RolloutConfig(
            phases=batched_as_phases(total, int(rollout.get("batch_size") or 1), rollout.get("delay_seconds")),
            delay_from_phase_start=True,
            order_by_last_seen=False,
        )
    if strategy == "phased":
        return RolloutConfig(
            phases=list(rollout.get("phases") or []),
            max_concurrent=rollout.get("max_concurrent"),
            failure_threshold_percent=rollout.get("failure_threshold_percent"),
            pending_timeout_seconds=rollout.get("pending_timeout_seconds"),
        )
    return None
