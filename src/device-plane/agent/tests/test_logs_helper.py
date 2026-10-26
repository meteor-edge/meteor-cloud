"""Root logs helper: only sources it lists, fixed argv, no options smuggled in names."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "packaging" / "meteorcli-logs"


def _load():
    loader = importlib.machinery.SourceFileLoader("meteorcli_logs", str(HELPER))
    spec = importlib.util.spec_from_loader("meteorcli_logs", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = _load()

UNITS = (
    "nginx.service loaded active running A high performance web server\n"
    "ghost.service not-found inactive dead ghost.service\n"
    "app@1.service loaded failed failed My app\n"
)
CONTAINERS = "web\trunning\tshop\nbad name\trunning\t\ndb\texited\t\n"


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch):
    def run(args, **_kwargs):
        if args[0] == "systemctl":
            return subprocess.CompletedProcess(args, 0, UNITS, "")
        if args[0] == "docker":
            return subprocess.CompletedProcess(args, 0, CONTAINERS, "")
        raise AssertionError(args)

    monkeypatch.setattr(helper.subprocess, "run", run)
    monkeypatch.setattr(helper.os.path, "exists", lambda path: path == "/var/run/docker.sock")
    monkeypatch.setattr(helper.os.path, "isdir", lambda path: path == "/run/systemd/system")
    monkeypatch.setattr(helper, "log_files", lambda: [])


def test_list_reports_journal_loaded_units_and_containers(fake_tools) -> None:
    sources = helper.list_sources()
    assert sources[0] == {"kind": "journal", "name": ""}
    assert {"kind": "systemd", "name": "nginx.service", "state": "active"} in sources
    assert {"kind": "systemd", "name": "app@1.service", "state": "failed"} in sources
    assert not any(item["name"] == "ghost.service" for item in sources)
    assert {"kind": "docker", "name": "web", "state": "running", "project": "shop"} in sources
    assert {"kind": "docker", "name": "db", "state": "exited"} in sources
    assert not any(item["name"] == "bad name" for item in sources)


@pytest.mark.parametrize(
    "argv",
    [
        ["systemd", "-n"],
        ["systemd"],
        ["bogus", "x"],
        ["journal", "--lines", "0"],
        ["journal", "--lines", "99999"],
        ["journal", "--since", "yesterday"],
        ["journal", "--grep", "a\nb"],
        ["journal", "--grep", "x" * 201],
        ["journal", "--output", "json"],
    ],
)
def test_bad_arguments_are_refused(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        helper.parse_show(argv)


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("systemd", "ghost.service"),
        ("systemd", "nginx.service;id"),
        ("systemd", "sshd"),
        ("docker", "other"),
        ("docker", "--help"),
        ("file", "/etc/shadow"),
        ("file", "/var/log/nginx/../../../etc/shadow"),
    ],
)
def test_sources_that_are_not_listed_are_refused(fake_tools, kind: str, name: str) -> None:
    with pytest.raises(SystemExit):
        helper.require_listed({"kind": kind, "name": name})


def test_listed_sources_are_accepted(fake_tools) -> None:
    helper.require_listed({"kind": "systemd", "name": "nginx.service"})
    helper.require_listed({"kind": "docker", "name": "web"})
    helper.require_listed({"kind": "journal", "name": ""})


def test_symlinked_log_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "secret"
    target.write_text("x")
    link = tmp_path / "access.log"
    link.symlink_to(target)
    monkeypatch.setattr(helper, "FILE_GLOBS", (str(tmp_path / "*.log"),))
    monkeypatch.setattr(helper, "LOG_ROOT", str(tmp_path) + "/")
    assert not helper.safe_file(str(link))
    real = tmp_path / "error.log"
    real.write_text("x")
    assert helper.safe_file(str(real))
    assert not helper.safe_file(str(tmp_path / "other.txt"))


def _opts(kind: str, name: str = "", **extra):
    return {"kind": kind, "name": name, "lines": 200, "since": None, "grep": None, "follow": False, **extra}


def test_commands_are_fixed_argv() -> None:
    assert helper.command(_opts("systemd", "nginx.service", since="15m"), lines=50, follow=True) == [
        "journalctl",
        "--no-pager",
        "--quiet",
        "--output=short-iso",
        "--lines=50",
        "--unit=nginx.service",
        "--since=-15min",
        "--follow",
    ]
    assert "--unit" not in " ".join(helper.command(_opts("journal"), lines=10, follow=False))
    assert helper.command(_opts("docker", "web", since="1h"), lines=0, follow=True) == [
        "docker",
        "logs",
        "--timestamps",
        "--tail",
        "0",
        "--since",
        "3600s",
        "--follow",
        "--",
        "web",
    ]
    assert helper.command(_opts("file", "/var/log/syslog"), lines=5, follow=True) == [
        "tail",
        "-n",
        "5",
        "-F",
        "--",
        "/var/log/syslog",
    ]


def test_parse_show_reads_options() -> None:
    opts = helper.parse_show(["docker", "web", "--lines", "500", "--since", "7d", "--grep", "Error", "--follow"])
    assert opts == {
        "kind": "docker",
        "name": "web",
        "lines": 500,
        "since": "7d",
        "grep": "Error",
        "follow": True,
    }


class _Proc:
    def __init__(self, lines: list[bytes]) -> None:
        self.stdout = iter(lines)

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        pass


def _capture(monkeypatch: pytest.MonkeyPatch, lines: list[bytes]) -> io.BytesIO:
    out = io.BytesIO()
    monkeypatch.setattr(helper.subprocess, "Popen", lambda *a, **k: _Proc(lines))
    monkeypatch.setattr(helper.sys, "stdout", type("S", (), {"buffer": out})())
    return out


def test_grep_keeps_last_n_matches_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _capture(monkeypatch, [b"ok 1\n", b"ERROR a\n", b"ok 2\n", b"error b\n", b"Error c\n"])
    helper.stream(["journalctl"], needle="error", keep=2)
    assert out.getvalue() == b"error b\nError c\n"


def test_stream_without_grep_copies_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _capture(monkeypatch, [b"a\n", b"b\n"])
    helper.stream(["journalctl"], needle=None)
    assert out.getvalue() == b"a\nb\n"


def test_must_run_as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit):
        helper.main(["list"])
