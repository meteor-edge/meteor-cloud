"""Gateway peer apply: add/remove via mocked wg."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


def test_apply_peers_keeps_static_peers() -> None:
    calls: list[list[str]] = []

    def runner(args):
        calls.append(list(args))
        result = MagicMock()
        result.stdout = ""
        if args[:3] == ["wg", "show", "wg0"] and "dump" in args:
            result.stdout = "private\t\nLINKKEY\t\t\t0\nOLDKEY\t\t\t0\n"
        return result

    apply_peers(
        interface="wg0",
        desired=[{"public_key": "LINKKEY", "allowed_ips": "10.8.0.9/32"}],
        keep=frozenset({"LINKKEY"}),
        runner=runner,
    )
    joined = [" ".join(item) for item in calls if "show" not in item]
    assert "wg set wg0 peer OLDKEY remove" in joined
    assert not any("LINKKEY" in line for line in joined)


class FakeWg:
    """In-memory `wg`: `show <if> dump` and `set <if> peer <key> allowed-ips|remove`."""

    def __init__(self, peers: dict[str, tuple[str, int]] | None = None) -> None:
        self.peers = dict(peers or {})
        self.sets: list[list[str]] = []

    def __call__(self, args):
        args = list(args)
        result = MagicMock()
        result.stdout = ""
        if args[:2] == ["wg", "show"] and args[-1] == "dump":
            lines = ["server-private\tserver-public\t51820\toff"]
            for key, (allowed, handshake) in self.peers.items():
                lines.append(f"{key}\t(none)\t203.0.113.9:4000\t{allowed}\t{handshake}\t0\t0\t25")
            result.stdout = "\n".join(lines) + "\n"
        elif args[:2] == ["wg", "set"]:
            self.sets.append(args)
            key = args[4]
            if args[5] == "remove":
                self.peers.pop(key, None)
            else:
                self.peers[key] = (args[6], self.peers.get(key, ("", 0))[1])
        return result


def test_reconcile_restores_missing_and_skips_unchanged_peers() -> None:
    # Gateway restart: wg0 came back empty; the agent restores every authorized peer.
    wg = FakeWg()
    desired = [
        {"public_key": "DEV1", "allowed_ips": "10.8.0.2/32"},
        {"public_key": "DEV2", "allowed_ips": "10.8.0.3/32"},
    ]
    apply_peers(interface="wg0", desired=desired, runner=wg)
    assert wg.peers == {"DEV1": ("10.8.0.2/32", 0), "DEV2": ("10.8.0.3/32", 0)}
    wg.sets.clear()
    # Nothing changed: no `wg set` at all on the next cycle.
    apply_peers(interface="wg0", desired=desired, runner=wg)
    assert wg.sets == []
    # A changed address is corrected in place.
    apply_peers(interface="wg0", desired=[{"public_key": "DEV1", "allowed_ips": "10.8.0.9/32"}], runner=wg)
    assert wg.peers == {"DEV1": ("10.8.0.9/32", 0)}


def test_reconcile_removes_revoked_peer() -> None:
    wg = FakeWg({"DEV1": ("10.8.0.2/32", 100), "REVOKED": ("10.8.0.3/32", 100)})
    apply_peers(interface="wg0", desired=[{"public_key": "DEV1", "allowed_ips": "10.8.0.2/32"}], runner=wg)
    assert set(wg.peers) == {"DEV1"}
    assert wg.sets == [["wg", "set", "wg0", "peer", "REVOKED", "remove"]]


def test_sync_reports_only_changed_handshakes_and_survives_api_outage(monkeypatch) -> None:
    import connectivity_agent

    wg = FakeWg({"DEV1": ("10.8.0.2/32", 1_700_000_000), "LINK": ("10.9.0.2/32", 1_700_000_000)})
    posts: list[dict] = []
    state = {"down": False}

    def fake_request(url, token, *, method="GET", body=None):
        if state["down"]:
            raise OSError("connection refused")
        if url.endswith("/peers"):
            return {"peers": [{"public_key": "DEV1", "allowed_ips": "10.8.0.2/32"}]}
        posts.append(body)
        return {"updated": len(body["items"])}

    monkeypatch.setattr(connectivity_agent, "_request", fake_request)
    reported: dict[str, int] = {}
    kwargs = {"api_url": "http://api", "token": "t", "interface": "wg0", "keep": frozenset({"LINK"}), "runner": wg}
    connectivity_agent.sync_once(**kwargs, reported=reported)
    assert [item["public_key"] for item in posts[0]["items"]] == ["DEV1"]
    connectivity_agent.sync_once(**kwargs, reported=reported)
    assert len(posts) == 1  # unchanged handshake: no POST
    wg.peers["DEV1"] = ("10.8.0.2/32", 1_700_000_120)
    connectivity_agent.sync_once(**kwargs, reported=reported)
    assert len(posts) == 2

    # Control plane down: the sync fails, but the peers on wg0 stay as they are.
    state["down"] = True
    with pytest.raises(OSError):
        connectivity_agent.sync_once(**kwargs, reported=reported)
    assert set(wg.peers) == {"DEV1", "LINK"}
