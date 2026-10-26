"""Tests for meteorcli.service state and duplicate-run detection."""

from __future__ import annotations

import subprocess

import pytest

from edge_agent import service


def _show(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    monkeypatch.setattr(
        service.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    )


def test_missing_systemctl_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(service.subprocess, "run", boom)
    assert service.service_state() == service.NOT_INSTALLED


@pytest.mark.parametrize(
    ("stdout", "state"),
    [
        ("LoadState=not-found\nActiveState=inactive\n", service.NOT_INSTALLED),
        ("LoadState=loaded\nActiveState=inactive\n", "inactive"),
        ("LoadState=loaded\nActiveState=failed\n", "failed"),
        ("LoadState=loaded\nActiveState=active\n", "active"),
    ],
)
def test_service_state(monkeypatch: pytest.MonkeyPatch, stdout: str, state: str) -> None:
    _show(monkeypatch, stdout)
    assert service.service_state() == state


def test_foreground_run_clashes_with_active_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    message = service.duplicate_run_message("active")
    assert message is not None
    assert "journalctl -u meteorcli -f" in message
    assert service.duplicate_run_message("inactive") is None


def test_the_unit_itself_does_not_clash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    assert service.duplicate_run_message("active") is None
