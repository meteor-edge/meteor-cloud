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
    monkeypatch.setattr(service, "running_as_service", lambda: False)
    message = service.duplicate_run_message("active")
    assert message is not None
    assert "journalctl -u meteorcli -f" in message
    assert service.duplicate_run_message("inactive") is None


def test_the_unit_itself_does_not_clash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "running_as_service", lambda: True)
    assert service.duplicate_run_message("active") is None


@pytest.mark.parametrize(
    ("cgroup", "expected"),
    [
        ("0::/system.slice/meteorcli.service\n", True),
        ("12:pids:/system.slice/meteorcli.service\n1:name=systemd:/system.slice/meteorcli.service\n", True),
        # A desktop terminal: INVOCATION_ID is inherited, but the cgroup is the terminal's.
        ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-term.scope\n", False),
        ("0::/system.slice/meteorcli-firstboot.service\n", False),
        ("0::/\n", False),
    ],
)
def test_running_as_service_reads_the_cgroup(tmp_path, monkeypatch, cgroup: str, expected: bool) -> None:
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    cgroup_file = tmp_path / "cgroup"
    cgroup_file.write_text(cgroup, encoding="utf-8")
    assert service.running_as_service(cgroup_file) is expected


def test_running_as_service_without_proc(tmp_path) -> None:
    assert service.running_as_service(tmp_path / "missing") is False
