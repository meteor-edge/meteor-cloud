"""Unit tests for the systemd runtime adapter (mocked systemctl)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from runtime.systemd import SYSTEMD_HELPER, SystemdAdapter, SystemdError, systemd_available


class FakeRunner:
    def __init__(self, *, active: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.active = active

    def __call__(self, args, *, check: bool = True):  # noqa: ANN001
        self.calls.append(list(args))
        if args[:2] == ["systemctl", "is-active"]:
            code = 0 if self.active else 1
            return subprocess.CompletedProcess(args, code, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")


def test_apply_writes_unit_reloads_enables_and_restarts(tmp_path: Path) -> None:
    unit_dir = tmp_path / "units"
    unit_file = tmp_path / "cam.service"
    unit_file.write_text("[Unit]\nDescription=Cam\n", encoding="utf-8")
    runner = FakeRunner(active=True)
    adapter = SystemdAdapter(unit_dir=unit_dir, runner=runner)

    adapter.apply(unit_name="cam.service", unit_file=unit_file, start=True)

    assert (unit_dir / "cam.service").read_text(encoding="utf-8").startswith("[Unit]")
    assert runner.calls[0] == ["systemctl", "daemon-reload"]
    assert runner.calls[1] == ["systemctl", "enable", "cam.service"]
    assert runner.calls[2] == ["systemctl", "restart", "cam.service"]


def test_apply_without_start_skips_restart(tmp_path: Path) -> None:
    unit_dir = tmp_path / "units"
    unit_file = tmp_path / "cam.service"
    unit_file.write_text("[Unit]\n", encoding="utf-8")
    runner = FakeRunner()
    adapter = SystemdAdapter(unit_dir=unit_dir, runner=runner)

    adapter.apply(unit_name="cam.service", unit_file=unit_file, start=False)

    assert ["systemctl", "restart", "cam.service"] not in runner.calls
    assert ["systemctl", "enable", "cam.service"] in runner.calls


def test_health_check_uses_is_active(tmp_path: Path) -> None:
    runner = FakeRunner(active=False)
    adapter = SystemdAdapter(unit_dir=tmp_path, runner=runner)
    assert adapter.health_check(unit_name="cam.service") is False
    assert runner.calls[-1][:3] == ["systemctl", "is-active", "--quiet"]


def test_apply_raises_on_systemctl_failure(tmp_path: Path) -> None:
    unit_file = tmp_path / "cam.service"
    unit_file.write_text("[Unit]\n", encoding="utf-8")

    def failing(args, *, check: bool = True):  # noqa: ANN001
        return subprocess.CompletedProcess(args, 1, "", "permission denied")

    adapter = SystemdAdapter(unit_dir=tmp_path / "units", runner=failing)
    with pytest.raises(SystemdError, match="permission denied"):
        adapter.apply(unit_name="cam.service", unit_file=unit_file)


def test_systemd_available_false_without_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runtime.systemd.shutil.which", lambda _name: None)
    assert systemd_available() is False


def test_apply_uses_helper_when_not_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runtime.systemd.os.geteuid", lambda: 1000)
    monkeypatch.setattr("runtime.systemd.systemd_helper_installed", lambda: True)
    unit_file = tmp_path / "seed-systemd.service"
    unit_file.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    runner = FakeRunner()
    adapter = SystemdAdapter(runner=runner)

    adapter.apply(unit_name="seed-systemd.service", unit_file=unit_file, start=True)

    assert runner.calls == [
        ["sudo", "-n", SYSTEMD_HELPER, "apply", "seed-systemd.service", str(unit_file)]
    ]


def test_apply_without_start_passes_no_start_to_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runtime.systemd.os.geteuid", lambda: 1000)
    monkeypatch.setattr("runtime.systemd.systemd_helper_installed", lambda: True)
    unit_file = tmp_path / "cam.service"
    unit_file.write_text("[Service]\n", encoding="utf-8")
    runner = FakeRunner()
    adapter = SystemdAdapter(runner=runner)

    adapter.apply(unit_name="cam.service", unit_file=unit_file, start=False)

    assert runner.calls[0][-1] == "--no-start"


def test_apply_without_helper_explains_installcli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runtime.systemd.os.geteuid", lambda: 1000)
    monkeypatch.setattr("runtime.systemd.systemd_helper_installed", lambda: False)
    unit_file = tmp_path / "cam.service"
    unit_file.write_text("[Service]\n", encoding="utf-8")
    adapter = SystemdAdapter(runner=FakeRunner())
    with pytest.raises(SystemdError, match="sudo ./installcli.sh"):
        adapter.apply(unit_name="cam.service", unit_file=unit_file)


def test_recover_uses_helper_when_not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runtime.systemd.os.geteuid", lambda: 1000)
    monkeypatch.setattr("runtime.systemd.systemd_helper_installed", lambda: True)
    runner = FakeRunner()
    adapter = SystemdAdapter(runner=runner)
    adapter.recover(unit_name="cam.service")
    assert runner.calls == [
        ["sudo", "-n", SYSTEMD_HELPER, "restart", "cam.service"]
    ]
