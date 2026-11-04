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
    active_names = {"Wired connection 1"}
    device_conn = {"wlan0": "old-wifi", "eth0": "Wired connection 1"}

    def run(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["/usr/sbin/rfkill", "unblock"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == [helper.NMCLI, "radio", "wifi"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:4] == [helper.NMCLI, "-t", "-f", "GENERAL.CONNECTION"]:
            iface = args[-1]
            value = device_conn.get(iface, "--")
            return subprocess.CompletedProcess(
                args, 0, f"GENERAL.CONNECTION:{value}\n", ""
            )
        if args[:4] == [helper.NMCLI, "-t", "-f", "NAME"] and "--active" in args:
            body = "".join(f"{n}\n" for n in active_names)
            return subprocess.CompletedProcess(args, 0, body, "")
        if args[:2] == [helper.NMCLI, "connection"] and args[2] == "up":
            idx = args.index("id")
            name = args[idx + 1]
            active_names.add(name)
            if name.startswith("meteorcloud-"):
                device_conn["wlan0"] = name
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == [helper.NMCLI, "connection"] and args[2] == "add":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == [helper.NMCLI, "connection"] and args[2] == "delete":
            idx = args.index("id")
            active_names.discard(args[idx + 1])
            return subprocess.CompletedProcess(args, 0, "", "")
        if "device" in args and "status" in args:
            return subprocess.CompletedProcess(args, 0, "wlan0:wifi\neth0:ethernet\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(helper.subprocess, "run", run)
    monkeypatch.setattr(helper.time, "sleep", lambda _s: None)
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


def test_apply_rejects_ethernet_iface(capsys, monkeypatch) -> None:
    body = json.dumps(
        {"op": "apply", "ssid": "Lab", "psk": "password12", "interface": "eth0"}
    )
    monkeypatch.setattr(helper.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(helper.os, "access", lambda _p, _m: True)
    monkeypatch.setattr(helper.sys, "stdin", type("S", (), {"read": lambda self, n=-1: body})())
    with pytest.raises(SystemExit) as exited:
        helper.main()
    assert exited.value.code == 2
    assert "wifi device" in capsys.readouterr().out


def test_apply_succeeds_when_ethernet_also_active(
    nmcli_calls: list[list[str]], capsys, monkeypatch
) -> None:
    """Regression: first active connection is often ethernet; wifi can still be up."""
    body = json.dumps(
        {
            "op": "apply",
            "ssid": "TP-Link_DEB9",
            "psk": "password12",
            "interface": "wlan0",
            "hidden": False,
        }
    )
    monkeypatch.setattr(helper.sys, "stdin", type("S", (), {"read": lambda self, n=-1: body})())
    with pytest.raises(SystemExit) as exited:
        helper.main()
    assert exited.value.code == 0, capsys.readouterr().out
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["connection"] == "meteorcloud-TP-Link_DEB9"
