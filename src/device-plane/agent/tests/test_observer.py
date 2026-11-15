"""Agent observer: app lifecycle, per-app metrics, network, Wi-Fi, errors and warnings."""

from __future__ import annotations

import json
import logging
import subprocess

from edge_agent import observer
from edge_agent.observer import (
    AppObserver,
    DeviceObserver,
    ErrorCounter,
    WarningForwarder,
    parse_inspect_line,
    parse_stats,
    read_network_bytes,
    read_wifi_signal_dbm,
)

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
    lo: 9000 10 0 0 0 0 0 0 9000 10 0 0 0 0 0 0
  eth0: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0
 wlan0: 500 5 0 0 0 0 0 0 100 1 0 0 0 0 0 0
docker0: 7777 1 0 0 0 0 0 0 7777 1 0 0 0 0 0 0
"""

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   54.  -56.  -256        0      0      0      0     12        0
"""


def test_network_bytes_skip_loopback_and_bridges() -> None:
    assert read_network_bytes(NET_DEV) == (1500, 2100)
    assert read_network_bytes("") is None


def test_wifi_signal_is_read_in_dbm() -> None:
    assert read_wifi_signal_dbm(WIRELESS) == -56.0
    assert read_wifi_signal_dbm(WIRELESS.replace("-56.", "70.")) is None
    assert read_wifi_signal_dbm("") is None


def test_network_rates_from_two_readings() -> None:
    now = [100.0]
    device = DeviceObserver(apps=AppObserver(runner=_docker({})), warnings=_no_warnings(), clock=lambda: now[0])
    assert device.network_rates((1000, 2000)) == {}
    now[0] = 160.0
    assert device.network_rates((7000, 2600)) == {"net_rx_bps": 100.0, "net_tx_bps": 10.0}
    now[0] = 220.0
    assert device.network_rates((10, 10)) == {}, "a counter reset gives no rate"


def test_error_counter_counts_errors_only() -> None:
    counter = ErrorCounter()
    log = logging.getLogger("edge_agent.test_observer")
    log.addHandler(counter)
    try:
        log.warning("not counted")
        log.error("one")
        log.exception("two")
    finally:
        log.removeHandler(counter)
    assert counter.take() == 2
    assert counter.take() == 0


def test_inspect_line_with_health_latency() -> None:
    state = parse_inspect_line(
        "/web|running|true|0|false|2|2026-11-15T10:00:00Z|healthy|1000000000,1003000000;2000000000,2012500000;"
    )
    assert state is not None
    assert (state.name, state.running, state.restarts, state.health, state.health_ms) == (
        "web",
        True,
        2,
        "healthy",
        12.5,
    )
    looping = parse_inspect_line("/db|restarting|true|137|true|5|2026-11-15T10:00:00Z|")
    assert looping is not None and not looping.running and looping.exit_code == 137 and looping.oom_killed
    assert looping.health is None
    assert parse_inspect_line("garbage") is None


def test_stats_parse_cpu_and_memory() -> None:
    assert parse_stats("web|12.50%|45.5MiB / 1.9GiB\ndb|0.00%|1.2GB / 4GB\n") == {
        "web": (12.5, 45.5 * 1024**2),
        "db": (0.0, 1.2e9),
    }


def _line(name, status="running", running=True, exit_code=0, oom=False, restarts=0, started="t1", health=""):
    return f"/{name}|{status}|{str(running).lower()}|{exit_code}|{str(oom).lower()}|{restarts}|{started}|{health}"


def _docker(lines: dict[str, str], stats: str = "") -> observer.Runner:
    def run(args):
        if args[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(args, 0, "\n".join(lines), "")
        if args[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(args, 0, "\n".join(lines[n] for n in args[4:]), "")
        if args[:2] == ["docker", "stats"]:
            return subprocess.CompletedProcess(args, 0, stats, "")
        raise AssertionError(args)

    return run


def test_apps_report_metrics_and_lifecycle_changes() -> None:
    now = [0.0]
    lines = {"web": _line("web", health="healthy|1,2000001;"), "db": _line("db")}
    apps = AppObserver(runner=lambda args: _docker(lines, "web|3.5%|10MiB / 1GiB")(args), clock=lambda: now[0])

    metrics, events = apps.observe()
    assert events == [], "the first look is the baseline"
    assert metrics["app.web.cpu"] == 3.5 and metrics["app.web.mem"] == 10 * 1024**2
    assert metrics["app.web.up"] == 1 and metrics["app.web.healthy"] == 1 and metrics["app.web.health_ms"] == 2.0
    assert metrics["app.db.restarts"] == 0 and "app.db.healthy" not in metrics

    now[0] = 60
    lines["web"] = _line("web", health="unhealthy|")
    lines["db"] = _line("db", status="exited", running=False, exit_code=137, oom=True)
    lines["cache"] = _line("cache")
    _, events = apps.observe()
    by_type = {(e["type"], e["app"]) for e in events}
    assert ("app.unhealthy", "web") in by_type
    assert ("app.exited", "db") in by_type and ("app.oom_killed", "db") in by_type
    assert ("app.started", "cache") in by_type
    exited = next(e for e in events if e["type"] == "app.exited")
    assert exited["exit_code"] == 137 and exited["oom_killed"] is True

    # Restarts piling up inside ten minutes: one crash_loop event, not one per look.
    for restarts, at in ((1, 120), (2, 180), (3, 240), (4, 300)):
        now[0] = at
        lines["cache"] = _line("cache", restarts=restarts, started=f"t{restarts}")
        _, events = apps.observe()
        loops = [e for e in events if e["type"] == "app.crash_loop"]
        assert len(loops) == (1 if restarts == 3 else 0), (restarts, events)
        assert any(e["type"] == "app.restarted" for e in events)

    del lines["db"]
    now[0] = 360
    _, events = apps.observe()
    assert {"type": "app.removed", "app": "db"} in events


def test_apps_without_docker_report_nothing() -> None:
    def missing(_args):
        raise FileNotFoundError("docker")

    assert AppObserver(runner=missing).observe() == ({}, [])


def _no_warnings() -> WarningForwarder:
    forwarder = WarningForwarder(
        runner=lambda _a: subprocess.CompletedProcess(_a, 0, "", ""), helper=lambda *a: list(a)
    )
    forwarder.available = lambda: False
    return forwarder


def test_warnings_follow_the_journal_cursor() -> None:
    calls: list[list[str]] = []
    out = "\n".join(
        [
            json.dumps(
                {
                    "cursor": "s=1;i=1",
                    "timestamp": "2026-11-15T10:00:00Z",
                    "priority": 3,
                    "unit": "x.service",
                    "source": "x",
                    "message": "disk failing",
                }
            ),
            "not json",
            json.dumps({"cursor": "s=1;i=2", "message": "x" * 5000}),
        ]
    )

    def run(args):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, out, "")

    forwarder = WarningForwarder(runner=run, helper=lambda *a: ["helper", *a])
    forwarder.available = lambda: True
    events = forwarder.collect()
    assert calls[0] == ["helper", "warnings"]
    assert events[0] == {
        "type": "log",
        "message": "disk failing",
        "priority": 3,
        "unit": "x.service",
        "source": "x",
        "timestamp": "2026-11-15T10:00:00Z",
    }
    assert len(events) == 2 and len(events[1]["message"]) == observer.MAX_LOG_MESSAGE
    forwarder.collect()
    assert calls[1] == ["helper", "warnings", "--after-cursor", "s=1;i=2"]


def test_device_observer_merges_everything(monkeypatch) -> None:
    monkeypatch.setattr(observer, "read_network_bytes", lambda: None)
    monkeypatch.setattr(observer, "read_wifi_signal_dbm", lambda: -61.0)
    device = DeviceObserver(apps=AppObserver(runner=_docker({"web": _line("web")})), warnings=_no_warnings())
    device.install()
    try:
        logging.getLogger("edge_agent").error("boom")
        metrics, events = device.observe()
    finally:
        logging.getLogger("edge_agent").removeHandler(device.errors)
    assert metrics["wifi_signal_dbm"] == -61.0 and metrics["agent_errors"] == 1 and metrics["app.web.up"] == 1
    assert events == []


def test_long_app_names_stay_distinct_and_short() -> None:
    a, b = "a" * 50 + "-web", "a" * 50 + "-worker"
    assert observer.metric_name("web") == "web"
    assert observer.metric_name(a) != observer.metric_name(b)
    assert all(len(f"app.{observer.metric_name(n)}.health_ms") <= 64 for n in (a, b))
    lines = {a: _line(a), b: _line(b, running=False, status="exited")}
    metrics, _ = AppObserver(runner=_docker(lines)).observe()
    assert metrics[f"app.{observer.metric_name(a)}.up"] == 1
    assert metrics[f"app.{observer.metric_name(b)}.up"] == 0


def test_failed_inspect_keeps_the_baseline() -> None:
    lines = {"web": _line("web")}
    healthy = _docker(lines)
    broken = {"on": False}

    def run(args):
        if broken["on"] and args[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(args, 1, "", "Cannot connect to the Docker daemon")
        return healthy(args)

    apps = AppObserver(runner=run)
    apps.observe()
    broken["on"] = True
    assert apps.observe() == ({}, [])
    broken["on"] = False
    _, events = apps.observe()
    assert events == [], "no false removed/started events after a failed look"
