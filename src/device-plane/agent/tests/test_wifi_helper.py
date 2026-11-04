"""meteorcli-wifi helper: JSON ops, no shell, mocked nmcli."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "packaging" / "meteorcli-wifi"


def _load():
    loader = importlib.machinery.SourceFileLoader("meteorcli_wifi", str(HELPER))
    spec = importlib.util.spec_from_loader("meteorcli_wifi", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = _load()


@pytest.fixture
def nmcli_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(helper.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(helper.os, "access", lambda _p, _m: True)

    def run(args, **_kwargs):
        calls.append(list(args))
        if args[:3] == [helper.NMCLI, "-t", "-f"] and "show" in args:
            return subprocess.CompletedProcess(args, 0, "old-wifi:wlan0:802-11-wireless\n", "")
        if args[:2] == [helper.NMCLI, "connection"] and args[2] == "up":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == [helper.NMCLI, "connection"] and args[2] == "add":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == [helper.NMCLI, "connection"] and args[2] == "delete":
            return subprocess.CompletedProcess(args, 0, "", "")
        if "device" in args and "status" in args:
            return subprocess.CompletedProcess(args, 0, "wlan0:wifi\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(helper.subprocess, "run", run)
    monkeypatch.setattr(helper.time, "sleep", lambda _s: None)

    # After up, active_connection should see the new name.
    def active_side_effect():
        # Prefer last connection up id.
        for call in reversed(calls):
            if call[:3] == [helper.NMCLI, "connection", "up"] and "id" in call:
                idx = call.index("id")
                return call[idx + 1]
        return "old-wifi"

    monkeypatch.setattr(helper, "active_connection", active_side_effect)
    return calls


def test_inspect_reports_wifi(nmcli_calls: list[list[str]], capsys, monkeypatch) -> None:
    monkeypatch.setattr(helper.sys, "stdin", type("S", (), {"read": lambda self, n=-1: '{"op":"inspect"}'})())
    with pytest.raises(SystemExit) as exited:
        helper.main()
    assert exited.value.code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["wifi"] is True


def test_apply_rejects_shell_iface(capsys, monkeypatch) -> None:
    body = json.dumps(
        {"op": "apply", "ssid": "Lab", "psk": "password12", "interface": "wlan0;id"}
    )
    monkeypatch.setattr(helper.sys, "stdin", type("S", (), {"read": lambda self, n=-1: body})())
    with pytest.raises(SystemExit) as exited:
        helper.main()
    assert exited.value.code == 2
    assert "invalid interface" in capsys.readouterr().out


def test_apply_calls_nmcli_without_shell(nmcli_calls: list[list[str]], capsys, monkeypatch) -> None:
    body = json.dumps(
        {
            "op": "apply",
            "ssid": "Lab",
            "psk": "password12",
            "interface": "wlan0",
            "hidden": False,
        }
    )
    monkeypatch.setattr(helper.sys, "stdin", type("S", (), {"read": lambda self, n=-1: body})())
    with pytest.raises(SystemExit) as exited:
        helper.main()
    assert exited.value.code == 0
    assert any(c[:3] == [helper.NMCLI, "connection", "add"] for c in nmcli_calls)
    for call in nmcli_calls:
        assert all(isinstance(part, str) for part in call)
        joined = " ".join(call)
        assert "shell" not in joined
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["ssid"] == "Lab"
