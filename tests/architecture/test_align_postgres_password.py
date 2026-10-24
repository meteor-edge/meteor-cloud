"""Bundled Postgres helper resets the role password after a secret change."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO
    / "deploy"
    / "ansible"
    / "roles"
    / "platform_deployment"
    / "files"
    / "align-postgres-password.sh"
)


def _run(
    tmp_path: Path,
    *args: str,
    password: str = "secret",
    psql_rc: int = 0,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "psql.log"
    psql = bin_dir / "psql"
    psql.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'printf "%s\\n" "PGPASSWORD=${{PGPASSWORD-}}" >> "{log}"\n'
        f"exit {psql_rc}\n",
        encoding="utf-8",
    )
    psql.chmod(psql.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["POSTGRES_USER"] = "edge_platform"
    env["POSTGRES_DB"] = "edge_platform"
    env["POSTGRES_PASSWORD"] = password
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )


def test_align_escapes_quotes_and_uses_local_socket(tmp_path: Path) -> None:
    proc = _run(tmp_path, "align", password="o'reilly")
    assert proc.returncode == 0, proc.stderr
    logged = (tmp_path / "psql.log").read_text(encoding="utf-8")
    assert "ALTER ROLE \"edge_platform\" PASSWORD 'o''reilly'" in logged
    assert "-h " not in logged
    assert "PGPASSWORD=" in logged.splitlines()[-1]
    assert logged.splitlines()[-1] == "PGPASSWORD="


def test_check_uses_network_hostname_and_password(tmp_path: Path) -> None:
    proc = _run(tmp_path, "check", password="new-secret", psql_rc=2)
    assert proc.returncode == 2
    logged = (tmp_path / "psql.log").read_text(encoding="utf-8")
    assert "-h postgres" in logged
    assert "PGPASSWORD=new-secret" in logged


def test_ansible_execs_copied_helper_not_stdin() -> None:
    tasks = (
        REPO
        / "deploy"
        / "ansible"
        / "roles"
        / "platform_deployment"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    assert "align-postgres-password.sh" in tasks
    assert "sh -s --" not in tasks
    assert "postgres:/tmp/align-postgres-password.sh" in tasks
    assert "sh /tmp/align-postgres-password.sh align" in tasks
