"""Agent side of device logs: list through the helper, report on heartbeat, throttle."""

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


def test_lists_and_cleans_helper_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs, "helper_installed", lambda: True)
    raw = [
        {"kind": "journal", "name": ""},
        {"kind": "systemd", "name": "nginx.service", "state": "active", "extra": "dropped"},
        {"kind": "docker", "name": "web", "state": "running", "project": "shop"},
        {"kind": "file", "name": "/var/log/nginx/access.log", "size": 10},
        {"kind": "shell", "name": "x"},
        "junk",
    ]
    sources = logs.list_log_sources(runner=lambda _args: _ok(json.dumps(raw)))
    assert sources == [
        {"kind": "journal", "name": ""},
        {"kind": "systemd", "name": "nginx.service", "state": "active"},
        {"kind": "docker", "name": "web", "state": "running", "project": "shop"},
        {"kind": "file", "name": "/var/log/nginx/access.log", "size": 10},
    ]


def test_failed_helper_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs, "helper_installed", lambda: True)
    failed = subprocess.CompletedProcess([], 1, "", "sudo: a password is required")
    assert logs.list_log_sources(runner=lambda _args: failed) is None
    assert logs.list_log_sources(runner=lambda _args: _ok("not json")) is None


def test_cache_relists_only_after_refresh_interval() -> None:
    now = [0.0]
    calls: list[int] = []

    def lister():
        calls.append(1)
        return [{"kind": "journal", "name": ""}]

    cache = logs.LogSourceCache(lister=lister, clock=lambda: now[0], refresh_seconds=300)
    payload = logs.add_log_sources({}, cache)
    assert payload["log_sources"] == [{"kind": "journal", "name": ""}]
    now[0] = 299
    cache.get()
    assert len(calls) == 1
    now[0] = 300
    cache.get()
    assert len(calls) == 2


def test_payload_without_sources_has_no_key() -> None:
    cache = logs.LogSourceCache(lister=lambda: None)
    assert logs.add_log_sources({"capabilities": {}}, cache) == {"capabilities": {}}
