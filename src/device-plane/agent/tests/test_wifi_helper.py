"""Root helper behind the agent's sudo rule for Wi-Fi: one keyfile, fixed nmcli calls, rollback."""

from __future__ import annotations

import configparser
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "packaging" / "meteorcli-wifi"
CHANGE_ID = "5b0c1a52-7c0e-4f39-9a52-0d5b1f6a9c11"


def _load():
    loader = importlib.machinery.SourceFileLoader("meteorcli_wifi", str(HELPER))
    spec = importlib.util.spec_from_loader("meteorcli_wifi", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = _load()


class FakeNetworkManager:
    """Enough of nmcli for the helper: profiles are the keyfiles in the connections directory."""

    def __init__(self, directory: Path, *, air: dict[str, str | None], wired: bool = False) -> None:
        self.directory = directory
        self.air = air  # networks in range: ssid -> password (None: open)
        self.wired = wired
        self.active: str | None = None  # uuid
        self.profiles: dict[str, dict[str, str]] = {}
        self.extra: list[dict[str, str]] = []  # profiles that live outside the directory
        self.calls: list[tuple[str, ...]] = []
        self.reload()

    def reload(self) -> None:
        self.profiles = {}
        for path in sorted(self.directory.glob("*.nmconnection")):
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(path, encoding="utf-8")
            profile = {
                "name": parser["connection"]["id"],
                "uuid": parser["connection"]["uuid"],
                "file": str(path),
                "ssid": parser["wifi"]["ssid"],
                "psk": parser["wifi-security"]["psk"] if parser.has_section("wifi-security") else "",
            }
            self.profiles[profile["uuid"]] = profile
        for profile in self.extra:
            self.profiles[profile["uuid"]] = profile
        if self.active not in self.profiles:
            self.active = None

    def _up(self, profile: dict[str, str] | None) -> int:
        if profile is None or profile["ssid"] not in self.air:
            return 10
        if (self.air[profile["ssid"]] or "") != profile["psk"]:
            return 4
        self.active = profile["uuid"]
        return 0

    def __call__(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        out, code = "", 0
        ssid_of_active = self.profiles[self.active]["ssid"] if self.active else None
        if args == ("-t", "-f", "RUNNING", "general"):
            out = "running\n"
        elif args == ("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"):
            name = self.profiles[self.active]["name"] if self.active else ""
            out = f"wlan0:wifi:{'connected' if self.active else 'disconnected'}:{name}\n"
            out += f"eth0:ethernet:{'connected' if self.wired else 'unavailable'}:\n"
            out += "lo:loopback:connected (externally):lo\n"
        elif args[:3] == ("-t", "-f", "ACTIVE,SSID,SIGNAL"):
            for ssid in self.air:
                escaped = ssid.replace("\\", "\\\\").replace(":", "\\:")
                out += f"{'yes' if ssid == ssid_of_active else 'no'}:{escaped}:71\n"
        elif args == ("-t", "-f", "NAME,UUID,TYPE,FILENAME", "connection", "show"):
            for profile in self.profiles.values():
                out += f"{profile['name']}:{profile['uuid']}:802-11-wireless:{profile['file']}\n"
            out += "Wired connection 1:aaaa:802-3-ethernet:/run/NetworkManager/system-connections/w.nmconnection\n"
        elif args[:6] == ("-e", "no", "-g", "802-11-wireless.ssid", "connection", "show"):
            out = self.profiles[args[7]]["ssid"] + "\n"
        elif args == ("-t", "-f", "UUID,TYPE", "connection", "show", "--active"):
            out = f"{self.active}:802-11-wireless\n" if self.active else ""
        elif args == ("connection", "reload"):
            self.reload()
        elif args[:1] == ("--wait",) and args[2:5] == ("connection", "up", "id"):
            code = self._up(next((p for p in self.profiles.values() if p["name"] == args[5]), None))
        elif args[:3] == ("connection", "up", "uuid"):
            code = self._up(self.profiles.get(args[3]))
        elif args[:2] == ("device", "connect"):
            code = next((0 for p in self.profiles.values() if self._up(p) == 0), 4)
        elif args[:3] == ("connection", "delete", "uuid"):
            profile = self.profiles.get(args[3])
            if profile and os.path.exists(profile["file"]):
                os.unlink(profile["file"])
            self.reload()
        else:  # pragma: no cover - a call the fake does not know is a test bug
            raise AssertionError(f"unexpected nmcli call: {args}")
        return subprocess.CompletedProcess(list(args), code, out, "")


def _profile(directory: Path, name: str, ssid: str, psk: str, uuid: str) -> Path:
    path = directory / f"{name}.nmconnection"
    path.write_text(
        f"[connection]\nid={name}\nuuid={uuid}\ntype=wifi\n\n[wifi]\nssid={ssid}\n\n"
        f"[wifi-security]\nkey-mgmt=wpa-psk\npsk={psk}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def nm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeNetworkManager:
    connections = tmp_path / "system-connections"
    connections.mkdir()
    state = tmp_path / "state"
    monkeypatch.setattr(helper, "CONNECTIONS_DIR", str(connections))
    monkeypatch.setattr(helper, "STATE_DIR", str(state))
    monkeypatch.setattr(helper, "PENDING", str(state / "pending.json"))
    monkeypatch.setattr(helper, "BACKUP_DIR", str(state / "backup"))
    monkeypatch.setattr(helper, "NMCLI", sys.executable)  # only so that "is it installed" passes
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    _profile(connections, "preconfigured", "Home", "old-password", "uuid-home")
    fake = FakeNetworkManager(connections, air={"Home": "old-password", "Office": "office-secret"})
    fake.active = "uuid-home"
    monkeypatch.setattr(helper, "nmcli", fake)
    return fake


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str, request: dict | None = None
):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) if request is not None else ""))
    code = helper.main([command])
    return code, json.loads(capsys.readouterr().out)


def _apply(ssid: str, password: str | None, **extra) -> dict:
    return {"id": CHANGE_ID, "revision": 3, "ssid": ssid, "security": "wpa-psk", "password": password, **extra}


def test_status_lists_networks_without_passwords(nm, monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "status")
    assert code == 0
    assert out["supported"] is True
    assert out["connected_ssid"] == "Home"
    assert out["signal_percent"] == 71
    assert out["interface"] == "wlan0"
    assert out["wired_connected"] is False
    assert out["saved_networks"] == [{"ssid": "Home", "managed": False}]
    assert "old-password" not in json.dumps(out)
    assert "pending" not in out


def test_save_for_later_writes_a_root_only_keyfile_and_does_not_switch(nm, monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "apply", _apply("Office", "office-secret", hidden=True))
    assert (code, out) == (0, {"applied": True, "connected": False})
    path = Path(helper.CONNECTIONS_DIR) / f"{helper.profile_name('Office')}.nmconnection"
    text = path.read_text(encoding="utf-8")
    assert "ssid=Office" in text and "psk=office-secret" in text and "hidden=true" in text
    assert "autoconnect-priority=10" in text
    assert path.stat().st_mode & 0o777 == 0o600
    assert nm.active == "uuid-home"
    assert not os.path.exists(helper.PENDING)
    _code, status = _run(monkeypatch, capsys, "status")
    assert {"ssid": "Office", "managed": True} in status["saved_networks"]


def test_connect_now_switches_and_waits_for_confirm(nm, monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "apply", _apply("Office", "office-secret", connect_now=True))
    assert (code, out) == (0, {"applied": True, "connected": True})
    assert nm.profiles[nm.active]["ssid"] == "Office"
    _code, status = _run(monkeypatch, capsys, "status")
    assert status["pending"] == {"id": CHANGE_ID, "revision": 3}

    assert _run(monkeypatch, capsys, "confirm") == (0, {"confirmed": True})
    assert not os.path.exists(helper.PENDING)
    assert not os.path.exists(helper.BACKUP_DIR)
    assert nm.profiles[nm.active]["ssid"] == "Office"


def test_wrong_password_is_rolled_back_by_the_helper(nm, monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "apply", _apply("Home", "not-the-password", connect_now=True))
    assert code == helper.EXIT_NOT_JOINED
    assert "old Wi-Fi settings were put back" in out["error"]
    assert "not-the-password" not in out["error"]
    # The old profile is back, in use, and nothing of the new one is left.
    assert nm.active == "uuid-home"
    assert nm.profiles["uuid-home"]["psk"] == "old-password"
    assert [p.name for p in Path(helper.CONNECTIONS_DIR).iterdir()] == ["preconfigured.nmconnection"]
    assert not os.path.exists(helper.PENDING)


def test_rollback_after_the_agent_could_not_reach_the_server(nm, monkeypatch, capsys) -> None:
    # The new password joins (say the router changed), but the agent then finds no way to MeteorCloud.
    nm.air["Home"] = "new-password"
    code, _out = _run(monkeypatch, capsys, "apply", _apply("Home", "new-password", connect_now=True))
    assert code == 0
    assert nm.profiles[nm.active]["psk"] == "new-password"
    nm.air["Home"] = "old-password"

    code, out = _run(monkeypatch, capsys, "rollback")
    assert (code, out) == (0, {"rolled_back": True, "id": CHANGE_ID, "revision": 3})
    assert nm.active == "uuid-home"
    assert nm.profiles["uuid-home"]["psk"] == "old-password"
    assert len(nm.profiles) == 1
    assert _run(monkeypatch, capsys, "rollback") == (0, {"rolled_back": False})


def test_a_second_change_waits_for_the_first(nm, monkeypatch, capsys) -> None:
    _run(monkeypatch, capsys, "apply", _apply("Office", "office-secret", connect_now=True))
    code, out = _run(monkeypatch, capsys, "apply", _apply("Home", "another-password"))
    assert code == helper.EXIT_BAD_REQUEST
    assert "waiting for confirm or rollback" in out["error"]


def test_remove_refuses_to_cut_the_device_off(nm, monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "remove", {"ssid": "Home"})
    assert code == helper.EXIT_WOULD_CUT_OFF
    assert "no other connection" in out["error"]
    assert "uuid-home" in nm.profiles

    nm.wired = True
    assert _run(monkeypatch, capsys, "remove", {"ssid": "Home"}) == (0, {"removed": True, "found": True})
    assert nm.profiles == {}
    assert _run(monkeypatch, capsys, "remove", {"ssid": "Home"}) == (0, {"removed": True, "found": False})


class FakeNetplan:
    """`netplan set <key>=null` and `generate`: generated profiles go once their definition does."""

    def __init__(self, nm: FakeNetworkManager, *, refuse: bool = False) -> None:
        self.nm = nm
        self.refuse = refuse
        self.calls: list[tuple[str, ...]] = []
        self.removed: set[str] = set()

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        code = 0
        if args[0] == "set":
            code = 1 if self.refuse else 0
            if not self.refuse:
                self.removed.add(args[1].removesuffix("=null"))
        elif args == ("generate",):
            self.nm.extra = [p for p in self.nm.extra if not self._gone(p)]
        return subprocess.CompletedProcess(list(args), code, "", "")

    def _gone(self, profile: dict[str, str]) -> bool:
        definition = helper.netplan_definition(profile)
        return f"wifis.{definition}" in self.removed or (
            f"wifis.{definition}.access-points.{profile['ssid']}" in self.removed
        )


def _netplan_profile(nm: FakeNetworkManager, ssid: str, definition: str = "wlan0") -> None:
    name = f"netplan-{definition}-{ssid}"
    nm.extra.append(
        {
            "name": name,
            "uuid": f"uuid-{ssid.lower()}",
            "file": f"/run/NetworkManager/system-connections/{name}.nmconnection",
            "ssid": ssid,
            "psk": "x",
        }
    )
    nm.reload()


@pytest.fixture
def netplan(nm: FakeNetworkManager, monkeypatch: pytest.MonkeyPatch) -> FakeNetplan:
    fake = FakeNetplan(nm)
    monkeypatch.setattr(helper, "NETPLAN", sys.executable)  # only so that "is it installed" passes
    monkeypatch.setattr(helper, "netplan", fake)
    return fake


def test_a_network_from_the_os_configuration_is_removed_from_netplan(nm, netplan, monkeypatch, capsys) -> None:
    # What Raspberry Pi Imager leaves: the network in use comes from /etc/netplan, a cable is in too.
    _netplan_profile(nm, "TP-Link_DEB9")
    nm.active, nm.wired = "uuid-tp-link_deb9", True
    assert _run(monkeypatch, capsys, "remove", {"ssid": "TP-Link_DEB9"}) == (0, {"removed": True, "found": True})
    assert netplan.calls == [("set", "wifis.wlan0=null"), ("generate",)]
    assert "uuid-tp-link_deb9" not in nm.profiles
    assert nm.active is None
    assert "uuid-home" in nm.profiles


def test_only_that_network_goes_when_the_definition_has_others(nm, netplan, monkeypatch, capsys) -> None:
    _netplan_profile(nm, "Cafe")
    _netplan_profile(nm, "Shop")
    assert _run(monkeypatch, capsys, "remove", {"ssid": "Cafe"}) == (0, {"removed": True, "found": True})
    assert ("set", "wifis.wlan0.access-points.Cafe=null") in netplan.calls
    assert "uuid-cafe" not in nm.profiles
    assert "uuid-shop" in nm.profiles


def test_a_network_netplan_will_not_drop_is_reported(nm, netplan, monkeypatch, capsys) -> None:
    netplan.refuse = True
    _netplan_profile(nm, "Cafe")
    code, out = _run(monkeypatch, capsys, "remove", {"ssid": "Cafe"})
    assert code == helper.EXIT_UNSUPPORTED
    assert "netplan-wlan0-Cafe" in out["error"]
    assert "could not be removed" in out["error"]


def test_without_netplan_a_generated_network_is_reported(nm, monkeypatch, capsys) -> None:
    monkeypatch.setattr(helper, "NETPLAN", "/nonexistent/netplan")
    _netplan_profile(nm, "Cafe")
    code, out = _run(monkeypatch, capsys, "remove", {"ssid": "Cafe"})
    assert code == helper.EXIT_UNSUPPORTED
    assert "netplan-wlan0-Cafe" in out["error"]


@pytest.mark.parametrize(
    ("name", "file", "expected"),
    [
        ("netplan-wlan0-Cafe", "/run/NetworkManager/system-connections/netplan-wlan0-Cafe.nmconnection", "wlan0"),
        ("Cafe", "/run/NetworkManager/system-connections/netplan-NM-1b2c.nmconnection", "NM-1b2c"),
        ("preconfigured", "/etc/NetworkManager/system-connections/preconfigured.nmconnection", None),
        ("netplan-a.b-Cafe", "/run/NetworkManager/system-connections/netplan-a.b-Cafe.nmconnection", None),
    ],
)
def test_netplan_definition_of_a_profile(name: str, file: str, expected: str | None) -> None:
    assert helper.netplan_definition({"name": name, "file": file, "ssid": "Cafe"}) == expected


@pytest.mark.parametrize(
    "request_body",
    [
        _apply("", "long-enough"),
        _apply("x" * 33, "long-enough"),
        _apply("Home\nevil=1", "long-enough"),
        _apply("Home", "short"),
        _apply("Home", "x" * 64),
        _apply("Home", "pass\n[connection]"),
        _apply("Home", None),
        {**_apply("Home", "long-enough"), "security": "wpa-eap"},
        {**_apply("Home", "long-enough", connect_now=True), "id": "../../etc"},
    ],
)
def test_bad_requests_change_nothing(nm, monkeypatch, capsys, request_body: dict) -> None:
    before = sorted(p.name for p in Path(helper.CONNECTIONS_DIR).iterdir())
    code, out = _run(monkeypatch, capsys, "apply", request_body)
    assert code == helper.EXIT_BAD_REQUEST
    assert "error" in out
    assert sorted(p.name for p in Path(helper.CONNECTIONS_DIR).iterdir()) == before


def test_keyfile_values_cannot_start_a_new_setting(nm, monkeypatch, capsys) -> None:
    ssid = "Caf\u00e9; Bar" + chr(92) + "1 "
    code, _out = _run(monkeypatch, capsys, "apply", _apply(ssid, " pa;ss" + chr(92) + "word "))
    assert code == 0
    path = Path(helper.CONNECTIONS_DIR) / f"{helper.profile_name(ssid)}.nmconnection"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "ssid=Caf\u00e9" + chr(92) + "; Bar" + chr(92) * 2 + "1" + chr(92) + "s" in lines
    assert "psk=" + chr(92) + "spa" + chr(92) + ";ss" + chr(92) * 2 + "word" + chr(92) + "s" in lines


def test_terse_lines_keep_colons_in_names() -> None:
    assert helper.split_terse("yes:My\\:Net\\\\1:80") == ["yes", "My:Net\\1", "80"]


def test_not_root_or_unknown_command_is_refused(nm, monkeypatch, capsys) -> None:
    code, _out = _run(monkeypatch, capsys, "connect")
    assert code == helper.EXIT_BAD_REQUEST
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)
    assert helper.main(["status"]) == helper.EXIT_BAD_REQUEST


def test_without_network_manager_wifi_is_reported_unsupported(nm, monkeypatch, capsys) -> None:
    monkeypatch.setattr(helper, "NMCLI", "nmcli-that-is-not-installed")
    code, out = _run(monkeypatch, capsys, "status")
    assert code == 0 and out["supported"] is False
    assert "NetworkManager is not installed" in out["unsupported_reason"]
    code, out = _run(monkeypatch, capsys, "apply", _apply("Home", "long-enough"))
    assert code == helper.EXIT_UNSUPPORTED
