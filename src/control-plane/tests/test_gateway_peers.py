"""Gateway peer apply: add/remove via mocked wg."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

GATEWAY = Path(__file__).resolve().parents[2] / "device-plane" / "gateway"
sys.path.insert(0, str(GATEWAY))

from connectivity_agent import apply_peers  # noqa: E402


def test_apply_peers_adds_and_removes() -> None:
    calls: list[list[str]] = []

    def runner(args):
        calls.append(list(args))
        result = MagicMock()
        result.stdout = ""
        if args[:3] == ["wg", "show", "wg0"] and "dump" in args:
            result.stdout = "private\t\nOLDKEY\t\t\t0\n"
        return result

    apply_peers(
        interface="wg0",
        desired=[{"public_key": "NEWKEY", "allowed_ips": "10.8.0.5/32"}],
        runner=runner,
    )
    joined = [" ".join(item) for item in calls]
    assert any("peer OLDKEY remove" in line for line in joined)
    assert any("peer NEWKEY allowed-ips 10.8.0.5/32" in line for line in joined)
