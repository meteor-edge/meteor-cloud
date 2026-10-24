"""Tests for heartbeat sending and retry behavior."""

from __future__ import annotations

import pytest

from edge_agent.client import AgentApiError
from edge_agent.heartbeat import run_loop, send_heartbeat
from tests.conftest import FakeClient


def test_send_heartbeat_returns_status() -> None:
    client = FakeClient()
    response = send_heartbeat(client, "dev_secret")
    assert response["status"] == "online"
    assert client.heartbeat_calls[0]["device_token"] == "dev_secret"


def test_run_loop_stops_after_iterations() -> None:
    client = FakeClient()
    sleeps: list[float] = []
    remaining = {"n": 3}

    def should_continue() -> bool:
        if remaining["n"] <= 0:
            return False
        remaining["n"] -= 1
        return True

    run_loop(
        client,
        "dev_secret",
        interval_seconds=60,
        sleep=lambda seconds: sleeps.append(seconds),
        should_continue=should_continue,
    )
    assert len(client.heartbeat_calls) == 3
    assert sleeps == [60, 60, 60]


def test_run_loop_retries_transient_failure() -> None:
    client = FakeClient(heartbeat_error=AgentApiError(503, "unavailable", "down"))
    sleeps: list[float] = []
    remaining = {"n": 2}

    def should_continue() -> bool:
        if remaining["n"] <= 0:
            return False
        remaining["n"] -= 1
        return True

    run_loop(
        client,
        "dev_secret",
        interval_seconds=60,
        sleep=lambda seconds: sleeps.append(seconds),
        should_continue=should_continue,
        jitter=lambda low, high: high,
    )
    # Backoff grows exponentially rather than using the steady interval.
    assert sleeps == [1.0, 2.0]


def test_run_loop_backoff_is_jittered_and_capped() -> None:
    import random

    client = FakeClient(heartbeat_error=AgentApiError(0, "network_error", "timed out"))
    sleeps: list[float] = []
    remaining = {"n": 12}

    def should_continue() -> bool:
        remaining["n"] -= 1
        return remaining["n"] >= 0

    run_loop(
        client,
        "dev_secret",
        interval_seconds=60,
        sleep=sleeps.append,
        should_continue=should_continue,
        jitter=random.Random(7).uniform,
    )
    assert len(sleeps) == 12
    assert all(0.5 <= value <= 60.0 for value in sleeps)
    assert max(sleeps[-3:]) > 29  # reached the cap region (60s x 0.5..1.0)


def test_run_loop_raises_on_invalid_credential() -> None:
    client = FakeClient(heartbeat_error=AgentApiError(401, "invalid_device_credentials", "no"))
    with pytest.raises(AgentApiError):
        run_loop(
            client,
            "dev_secret",
            interval_seconds=60,
            sleep=lambda seconds: None,
            should_continue=lambda: True,
        )


def test_heartbeat_does_not_log_credential(caplog) -> None:
    client = FakeClient(heartbeat_error=AgentApiError(503, "unavailable", "down"))
    remaining = {"n": 1}

    def should_continue() -> bool:
        if remaining["n"] <= 0:
            return False
        remaining["n"] -= 1
        return True

    with caplog.at_level("WARNING", logger="edge_agent"):
        run_loop(
            client,
            "dev_super-secret",
            interval_seconds=60,
            sleep=lambda seconds: None,
            should_continue=should_continue,
        )
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "dev_super-secret" not in joined
