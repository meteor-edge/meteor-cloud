"""Host link snapshot parsing."""

from __future__ import annotations

import subprocess

from edge_agent.links import collect_network_links


def test_collect_network_links_from_nmcli(monkeypatch) -> None:
    def run(args, **_kwargs):
        assert args[0].endswith("nmcli")
        out = (
            "eth0:ethernet:connected:Wired connection 1\n"
            "wlan0:wifi:connected:meteorcloud-Lab\n"
            "lo:loopback:connected (externally):lo\n"
        )
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr("edge_agent.links.os.path.isfile", lambda _p: True)
    monkeypatch.setattr("edge_agent.links.os.access", lambda _p, _m: True)
    monkeypatch.setattr("edge_agent.links.subprocess.run", run)
    links = collect_network_links()
    assert links == [
        {
            "name": "eth0",
            "type": "ethernet",
            "state": "connected",
            "connection": "Wired connection 1",
        },
        {
            "name": "wlan0",
            "type": "wifi",
            "state": "connected",
            "connection": "meteorcloud-Lab",
        },
    ]
