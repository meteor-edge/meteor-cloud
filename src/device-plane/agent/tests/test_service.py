"""Tests for meteorcli.service inspection and duplicate-run detection."""

from __future__ import annotations

import subprocess

from edge_agent.service import ServiceStatus, duplicate_run_message, inspect_service


def _show(load: str, active: str, pid: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["systemctl"],
        0,
        stdout=f"LoadState={load}\nActiveState={active}\nMainPID={pid}\n",
        stderr="",
    )


def test_inspect_service_missing_systemctl() -> None:
    def runner() -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("systemctl")

    status = inspect_service(runner=runner, pid=lambda: 1, read_ppid=lambda _pid: None)
    assert status.installed is False
    assert status.active is False
    assert status.line() == "meteorcli.service is not installed"
    assert duplicate_run_message(status) is None


def test_inspect_service_not_loaded() -> None:
    status = inspect_service(
        runner=lambda: _show("not-found", "inactive", 0),
        pid=lambda: 1,
        read_ppid=lambda _pid: None,
    )
    assert status.installed is False
    assert duplicate_run_message(status) is None


def test_inspect_service_inactive() -> None:
    status = inspect_service(
        runner=lambda: _show("loaded", "inactive", 0),
        pid=lambda: 1,
        read_ppid=lambda _pid: None,
    )
    assert status.installed is True
    assert status.active is False
    assert status.line() == "meteorcli.service is installed but inactive"
    assert duplicate_run_message(status) is None


def test_inspect_service_failed() -> None:
    status = inspect_service(
        runner=lambda: _show("loaded", "failed", 0),
        pid=lambda: 1,
        read_ppid=lambda _pid: None,
    )
    assert status.line() == "meteorcli.service is failed"
    assert duplicate_run_message(status) is None


def test_duplicate_run_when_another_pid_is_active() -> None:
    status = inspect_service(
        runner=lambda: _show("loaded", "active", 99),
        pid=lambda: 1,
        read_ppid=lambda _pid: None,
    )
    assert status.this_process is False
    message = duplicate_run_message(status)
    assert message is not None
    assert "already running (pid 99)" in message
    assert "journalctl -u meteorcli -f" in message
    assert "systemctl stop meteorcli" in message


def test_this_process_is_the_unit() -> None:
    status = inspect_service(
        runner=lambda: _show("loaded", "active", 7),
        pid=lambda: 7,
        read_ppid=lambda _pid: 1,
    )
    assert status.this_process is True
    assert "this process" in status.line()
    assert duplicate_run_message(status) is None


def test_this_process_when_unit_is_an_ancestor() -> None:
    parents = {8: 7, 7: 1}

    status = inspect_service(
        runner=lambda: _show("loaded", "active", 7),
        pid=lambda: 8,
        read_ppid=lambda pid: parents.get(pid),
    )
    assert status.this_process is True
    assert duplicate_run_message(status) is None


def test_service_status_line_without_pid() -> None:
    status = ServiceStatus(installed=True, active=True, this_process=False, main_pid=0)
    assert status.line() == "meteorcli.service is active (another process)"
    assert "already running" in (duplicate_run_message(status) or "")
