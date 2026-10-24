"""make aws-up generates missing EDGE_PLATFORM_* secrets and reuses them."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ensure-platform-secrets.sh"


def _run(home: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (
        "EDGE_PLATFORM_POSTGRES_PASSWORD",
        "EDGE_PLATFORM_JWT_SECRET",
        "EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN",
        "EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD",
        "EDGE_PLATFORM_MQTT_INTERNAL_TOKEN",
        "EDGE_PLATFORM_ADMIN_EMAIL",
        "EDGE_PLATFORM_ADMIN_PASSWORD",
    ):
        env.pop(key, None)
    env["METEORCLOUD_HOME"] = str(home)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
    )


def test_generates_and_reuses_secrets(tmp_path: Path) -> None:
    first = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    path = tmp_path / "secrets.env"
    assert path.is_file()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    text = path.read_text(encoding="utf-8")
    assert "EDGE_PLATFORM_POSTGRES_PASSWORD=" in text
    assert "EDGE_PLATFORM_JWT_SECRET=" in text
    assert "EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN=" in text
    assert "EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD=" in text
    assert "EDGE_PLATFORM_MQTT_INTERNAL_TOKEN=" in text
    assert "EDGE_PLATFORM_ADMIN_EMAIL=" in text
    assert "EDGE_PLATFORM_ADMIN_PASSWORD=" in text

    second = _run(tmp_path)
    assert second.returncode == 0, second.stderr
    assert path.read_text(encoding="utf-8") == text


def test_exported_env_wins(tmp_path: Path) -> None:
    first = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    override = _run(
        tmp_path,
        {
            "EDGE_PLATFORM_POSTGRES_PASSWORD": "from-env",
            "EDGE_PLATFORM_JWT_SECRET": "jwt-from-env",
            "EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN": "conn-from-env",
        },
    )
    assert override.returncode == 0, override.stderr
    text = (tmp_path / "secrets.env").read_text(encoding="utf-8")
    assert "EDGE_PLATFORM_POSTGRES_PASSWORD=from-env" in text
    assert "EDGE_PLATFORM_JWT_SECRET=jwt-from-env" in text
    assert "EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN=conn-from-env" in text
