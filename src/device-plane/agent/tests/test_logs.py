"""Agent side of device logs: list through the root helper."""

from __future__ import annotations

import json
import subprocess

import pytest

from edge_agent import logs


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout, "")


def test_no_helper_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs, "helper_installed", lambda: False)
    assert logs.list_log_sources(runner=lambda _args: pytest.fail("must not run")) is None


def test_non_root_goes_through_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs.os, "geteuid", lambda: 1000)
    assert logs.helper_command("list") == ["sudo", "-n", logs.LOGS_HELPER, "list"]
    monkeypatch.setattr(logs.os, "geteuid", lambda: 0)
    assert logs.helper_command("list") == [logs.LOGS_HELPER, "list"]


def test_lists_helper_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs, "helper_installed", lambda: True)
    raw = [{"kind": "journal", "name": ""}, {"kind": "docker", "name": "web", "state": "running"}, "junk"]
    sources = logs.list_log_sources(runner=lambda _args: _ok(json.dumps(raw)))
    assert sources == raw[:2]


def test_failed_helper_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs, "helper_installed", lambda: True)
    failed = subprocess.CompletedProcess([], 1, "", "sudo: a password is required")
    assert logs.list_log_sources(runner=lambda _args: failed) is None
    assert logs.list_log_sources(runner=lambda _args: _ok("not json")) is None
