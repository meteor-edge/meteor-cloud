"""Root helper behind systemd deploys: caller-owned .service file, fixed systemctl argv."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "packaging" / "meteorcli-systemd"


def _load():
    loader = importlib.machinery.SourceFileLoader("meteorcli_systemd", str(HELPER))
    spec = importlib.util.spec_from_loader("meteorcli_systemd", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = _load()


@pytest.fixture
def unit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dest = tmp_path / "units"
    dest.mkdir()
    monkeypatch.setattr(helper, "UNIT_DIR", str(dest))
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    return dest


@pytest.fixture
def systemctl_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(helper.subprocess, "run", run)
    return calls


def _unit(path: Path, body: bytes = b"[Service]\nExecStart=/bin/true\n") -> Path:
    path.write_bytes(body)
    return path


def test_apply_writes_unit_enables_and_restarts(
    tmp_path: Path, unit_dir: Path, systemctl_calls: list[list[str]]
) -> None:
    src = _unit(tmp_path / "seed.service")
    helper.main(["apply", "seed-systemd.service", str(src)])
    assert (unit_dir / "seed-systemd.service").read_bytes() == src.read_bytes()
    assert os.listdir(unit_dir) == ["seed-systemd.service"]  # no temp file left behind
    assert systemctl_calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "seed-systemd.service"],
        ["systemctl", "restart", "seed-systemd.service"],
    ]


def test_apply_no_start_skips_restart(
    tmp_path: Path, unit_dir: Path, systemctl_calls: list[list[str]]
) -> None:
    src = _unit(tmp_path / "cam.service")
    helper.main(["apply", "cam.service", str(src), "--no-start"])
    assert (unit_dir / "cam.service").is_file()
    assert ["systemctl", "restart", "cam.service"] not in systemctl_calls
    assert ["systemctl", "enable", "cam.service"] in systemctl_calls


def test_refuses_meteorcli_service(tmp_path: Path, unit_dir: Path) -> None:
    src = _unit(tmp_path / "x.service")
    with pytest.raises(SystemExit):
        helper.main(["apply", "meteorcli.service", str(src)])
    assert not (unit_dir / "meteorcli.service").exists()


@pytest.mark.parametrize(
    "name",
    ["../escape.service", "/tmp/x.service", ".hidden.service", "cam", "cam.timer"],
)
def test_refuses_bad_unit_names(tmp_path: Path, unit_dir: Path, name: str) -> None:
    src = _unit(tmp_path / "x.service")
    with pytest.raises(SystemExit):
        helper.main(["apply", name, str(src)])


def test_refuses_unit_owned_by_someone_else(
    tmp_path: Path, unit_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _unit(tmp_path / "cam.service")
    monkeypatch.setenv("SUDO_UID", str(os.getuid() + 1))
    with pytest.raises(SystemExit):
        helper.main(["apply", "cam.service", str(src)])
    assert not (unit_dir / "cam.service").exists()


def test_refuses_missing_service_section(tmp_path: Path, unit_dir: Path) -> None:
    src = _unit(tmp_path / "cam.service", b"[Unit]\nDescription=nope\n")
    with pytest.raises(SystemExit):
        helper.main(["apply", "cam.service", str(src)])


def test_restart_runs_systemctl(unit_dir: Path, systemctl_calls: list[list[str]]) -> None:
    helper.main(["restart", "cam.service"])
    assert systemctl_calls == [["systemctl", "restart", "cam.service"]]


def test_refuses_when_not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit):
        helper.main(["restart", "cam.service"])


def test_unit_file_must_be_regular(tmp_path: Path, unit_dir: Path) -> None:
    target = tmp_path / "other"
    target.write_text("[Service]\n", encoding="utf-8")
    src = tmp_path / "cam.service"
    src.symlink_to(target)
    with pytest.raises(SystemExit):
        helper.main(["apply", "cam.service", str(src)])
    assert not (unit_dir / "cam.service").exists()


def test_write_unit_retries_short_writes(unit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_write = os.write

    def short_write(fd, data):
        return real_write(fd, bytes(data[:3]))

    monkeypatch.setattr(helper.os, "write", short_write)
    body = b"[Service]\nExecStart=/bin/true\n"
    helper.write_unit("short.service", body)
    assert (unit_dir / "short.service").read_bytes() == body


def test_write_unit_removes_its_temp_file_on_failure(unit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_replace(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr(helper.os, "replace", broken_replace)
    with pytest.raises(OSError):
        helper.write_unit("broken.service", b"[Service]\n")
    assert os.listdir(unit_dir) == []
