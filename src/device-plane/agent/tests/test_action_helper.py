"""Root helper behind the agent's sudo rule for restart, shut-down and SSH users."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "packaging" / "meteorcli-action"


def _load():
    loader = importlib.machinery.SourceFileLoader("meteorcli_action", str(HELPER))
    spec = importlib.util.spec_from_loader("meteorcli_action", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = _load()


class FakeUsers:
    def __init__(self) -> None:
        self.users: dict[str, str] = {"pi": "old-hash"}
        self.calls: list[tuple] = []
        self.started: list[list[str]] = []

    def exists(self, username: str) -> bool:
        return username in self.users

    def chpasswd(self, username: str, hashed: str) -> int:
        self.calls.append(("chpasswd", username, hashed))
        if username not in self.users:
            return 1
        self.users[username] = hashed
        return 0

    def useradd(self, username: str) -> int:
        self.calls.append(("useradd", username))
        if username in self.users:
            return 9
        self.users[username] = ""
        return 0


@pytest.fixture
def users(monkeypatch: pytest.MonkeyPatch) -> FakeUsers:
    fake = FakeUsers()
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper, "user_exists", fake.exists)

    def run_cmd(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        fake.calls.append(("run", args))
        if args[:1] == ("useradd",):
            return subprocess.CompletedProcess(list(args), fake.useradd(args[-1]), "", "")
        if args[:1] == ("userdel",):
            fake.users.pop(args[-1], None)
            return subprocess.CompletedProcess(list(args), 0, "", "")
        return subprocess.CompletedProcess(list(args), 0, "", "")

    def run_sub(argv, input=None, capture_output=True, text=True, timeout=30, check=False):  # noqa: A002
        if argv[:1] == ["chpasswd"]:
            username, password = input.strip().split(":", 1)
            return subprocess.CompletedProcess(argv, fake.chpasswd(username, password), "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    class Popen:
        def __init__(self, argv, start_new_session=False):
            fake.started.append(list(argv))

    monkeypatch.setattr(helper, "run", run_cmd)
    monkeypatch.setattr(helper.subprocess, "run", run_sub)
    monkeypatch.setattr(helper.subprocess, "Popen", Popen)
    monkeypatch.setattr(helper.shutil, "which", lambda name: f"/usr/sbin/{name}")
    return fake


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str, request: dict | None = None
):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) if request is not None else ""))
    code = helper.main([command])
    return code, json.loads(capsys.readouterr().out)


def test_reboot_and_shutdown_start_systemctl(users, monkeypatch, capsys) -> None:
    assert _run(monkeypatch, capsys, "reboot") == (0, {"started": True})
    assert users.started == [["systemctl", "reboot"]]
    assert _run(monkeypatch, capsys, "shutdown") == (0, {"started": True})
    assert users.started[-1] == ["systemctl", "poweroff"]


def test_set_password_updates_an_existing_user(users, monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "set-password", {"username": "pi", "password": "new-password"})
    assert (code, out) == (0, {"changed": True})
    assert users.users["pi"] == "new-password"
    code, out = _run(monkeypatch, capsys, "set-password", {"username": "missing", "password": "new-password"})
    assert code == helper.EXIT_BAD_REQUEST
    assert "no user" in out["error"]


def test_add_user_creates_home_and_refuses_reserved(users, monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(helper, "SUDOERS_DIR", str(tmp_path))
    home = tmp_path / "home" / "ops"
    home.mkdir(parents=True)

    class Info:
        pw_uid = 1001
        pw_gid = 1001

    monkeypatch.setattr(helper.pwd, "getpwnam", lambda name: Info())
    monkeypatch.setattr(helper.os.path, "expanduser", lambda path: str(home) if path == "~ops" else path)
    monkeypatch.setattr(helper.os, "chown", lambda *args: None)

    code, out = _run(
        monkeypatch,
        capsys,
        "add-user",
        {
            "username": "ops",
            "password": "ops-password",
            "sudo": True,
            "authorized_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJustATestKey you@laptop",
        },
    )
    assert (code, out) == (0, {"created": True})
    assert users.users["ops"] == "ops-password"
    assert (tmp_path / "90-meteorcli-ops").read_text(encoding="utf-8").startswith("ops ALL=")
    assert "ssh-ed25519" in (home / ".ssh" / "authorized_keys").read_text(encoding="utf-8")

    code, out = _run(monkeypatch, capsys, "add-user", {"username": "root", "password": "ops-password"})
    assert code == helper.EXIT_BAD_REQUEST
    assert "root" in out["error"]
