"""meteorcli release tarball contains agent + runtime only."""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "agent-dist.sh"


def test_agent_dist_packs_agent_and_runtime(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["AGENT_DIST_DIR"] = str(tmp_path)
    proc = subprocess.run(
        [str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    tarball = tmp_path / "meteorcli.tar.gz"
    assert tarball.is_file()
    assert (tmp_path / "install-meteorcli.sh").is_file()
    assert (tmp_path / "SHA256SUMS").is_file()

    names = tarfile.open(tarball, "r:gz").getnames()
    assert any(n.endswith("agent/installcli.sh") for n in names)
    assert any(n.endswith("agent/pyproject.toml") for n in names)
    assert any("/runtime/__init__.py" in n for n in names)
    assert not any("/agent/tests/" in n for n in names)
    assert not any(".ruff_cache" in n for n in names)
    assert not any("secrets" in n.lower() for n in names)
    assert not any(n.endswith(".env") for n in names)
