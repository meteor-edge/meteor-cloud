"""installcli.sh (the Python meteorcli installer) shell functions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_installcli_installs_wireguard_tools() -> None:
    installcli = Path(__file__).resolve().parents[1] / "installcli.sh"
    text = installcli.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(installcli)], check=True)
    assert "install_host_packages" in text
    assert "wireguard-tools" in text


INSTALLCLI = Path(__file__).resolve().parents[1] / "installcli.sh"


def _installcli_function(name: str) -> str:
    """One shell function from installcli.sh, so it can run without the install itself."""
    lines = INSTALLCLI.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}() {{")
    end = lines.index("}", start)
    return "\n".join(lines[start : end + 1])


def _run_installcli_function(name: str, script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"{_installcli_function(name)}\n{script}"],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def test_installcli_starts_the_service_by_default() -> None:
    text = INSTALLCLI.read_text(encoding="utf-8")
    assert "WITH_SERVICE=auto" in text
    assert "--no-service)" in text
    assert "systemctl restart meteorcli.service" in text
    # Packages and helpers before the service: it may bring up WireGuard at once.
    body = _installcli_function("install_meteorcli")
    assert body.index("install_root_helpers") < body.index("want_service")


def test_want_service_honours_explicit_flags() -> None:
    assert _run_installcli_function("want_service", "WITH_SERVICE=1; want_service").returncode == 0
    assert _run_installcli_function("want_service", "WITH_SERVICE=0; want_service").returncode == 1


def test_want_service_auto_skips_for_a_user_install() -> None:
    if os.geteuid() == 0:
        return
    result = _run_installcli_function("want_service", "WITH_SERVICE=auto; want_service")
    assert result.returncode == 1
    assert "no background service for a per-user install" in result.stderr


def test_need_writable_accepts_a_missing_home_local(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ok = _run_installcli_function("need_writable", f'need_writable "{home}/.local/share/meteorcli"')
    assert ok.returncode == 0
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o555)
    if os.geteuid() != 0:
        denied = _run_installcli_function("need_writable", f'need_writable "{locked}/a/b"')
        assert denied.returncode == 1

