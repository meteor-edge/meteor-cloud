from __future__ import annotations

import pytest

from edge_installer.exceptions import HealthCheckError
from edge_installer.health import checks


def test_verify_platform_retries_until_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter([(False, False), (True, False), (True, True)])
    monkeypatch.setattr(checks, "_probe", lambda base: next(results))
    monkeypatch.setattr(checks.time, "sleep", lambda seconds: None)

    report = checks.verify_platform("https://demo.example.com/", timeout_seconds=60)

    assert report.is_healthy


def test_verify_platform_fails_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def probe(base: str) -> tuple[bool, bool]:
        calls.append(base)
        return True, False

    monkeypatch.setattr(checks, "_probe", probe)
    monkeypatch.setattr(checks.time, "sleep", lambda seconds: None)

    with pytest.raises(HealthCheckError):
        checks.verify_platform("https://demo.example.com", timeout_seconds=0)

    assert calls == ["https://demo.example.com"]


def test_http_check_treats_connection_errors_as_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, timeout: int) -> None:
        raise ConnectionResetError("reset")

    monkeypatch.setattr(checks.request, "urlopen", boom)

    assert checks._http_check("https://demo.example.com/") is False


def test_verify_platform_waits_for_the_mqtt_port(monkeypatch: pytest.MonkeyPatch) -> None:
    reachable = iter([False, True])
    hosts: list[tuple[str, int]] = []

    def tcp(host: str, port: int) -> bool:
        hosts.append((host, port))
        return next(reachable)

    monkeypatch.setattr(checks, "_probe", lambda base: (True, True))
    monkeypatch.setattr(checks, "_tcp_check", tcp)
    monkeypatch.setattr(checks.time, "sleep", lambda seconds: None)

    report = checks.verify_platform("https://demo.example.com", timeout_seconds=60, mqtt_port=8883)

    assert report.mqtt == "healthy"
    assert hosts == [("demo.example.com", 8883)] * 2


def test_verify_platform_fails_when_mqtt_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "_probe", lambda base: (True, True))
    monkeypatch.setattr(checks, "_tcp_check", lambda host, port: False)
    monkeypatch.setattr(checks.time, "sleep", lambda seconds: None)

    with pytest.raises(HealthCheckError):
        checks.verify_platform("https://demo.example.com", timeout_seconds=0, mqtt_port=8883)


def test_mqtt_is_reported_disabled_without_a_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "_probe", lambda base: (True, True))

    report = checks.verify_platform("https://demo.example.com", timeout_seconds=0)

    assert report.mqtt == "disabled"
    assert report.is_healthy
