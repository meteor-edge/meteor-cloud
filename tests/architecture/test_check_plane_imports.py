"""Allowed vs forbidden plane imports for scripts/check_plane_imports.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_plane_imports.py"

sys.path.insert(0, str(REPO / "scripts"))
from check_plane_imports import check_tree


def test_this_repository_has_no_cross_plane_imports() -> None:
    assert check_tree(REPO) == []


def test_script_exits_zero_on_this_repository() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(REPO)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_detects_forbidden_import(tmp_path: Path) -> None:
    (tmp_path / "src/data-plane/data_plane").mkdir(parents=True)
    (tmp_path / "src/data-plane/data_plane/bad.py").write_text(
        "from app.core.config import Settings\n",
        encoding="utf-8",
    )
    hits = check_tree(tmp_path)
    assert hits
    assert any("forbidden import app" in line for line in hits)


def test_allows_same_plane_import(tmp_path: Path) -> None:
    (tmp_path / "src/data-plane/data_plane").mkdir(parents=True)
    (tmp_path / "src/data-plane/data_plane/ok.py").write_text(
        "from data_plane.main import app\n",
        encoding="utf-8",
    )
    assert check_tree(tmp_path) == []


def test_control_plane_must_not_import_agent(tmp_path: Path) -> None:
    (tmp_path / "src/control-plane/app").mkdir(parents=True)
    (tmp_path / "src/control-plane/app/leak.py").write_text(
        "import edge_agent.client\n",
        encoding="utf-8",
    )
    hits = check_tree(tmp_path)
    assert any("edge_agent" in line for line in hits)


def test_relative_imports_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "src/device-plane/agent").mkdir(parents=True)
    (tmp_path / "src/device-plane/agent/local.py").write_text(
        "from .client import Agent\n",
        encoding="utf-8",
    )
    assert check_tree(tmp_path) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
