"""Root helper behind the agent's sudo rule: only meteorcli's own wg0 config gets through."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from edge_agent.wireguard import write_client_config

HELPER = Path(__file__).resolve().parents[1] / "packaging" / "meteorcli-wg"
KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _load():
    loader = importlib.machinery.SourceFileLoader("meteorcli_wg", str(HELPER))
    spec = importlib.util.spec_from_loader("meteorcli_wg", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = _load()


def _agent_config(tmp_path: Path) -> str:
    conf, _ = write_client_config(
        tmp_path,
        private_key=KEY,
        address="10.8.0.9/32",
        server_public_key=KEY,
        server_endpoint="gw.example.com:51820",
    )
    return conf.read_text(encoding="utf-8")


def test_agent_config_passes_unchanged_in_meaning(tmp_path: Path) -> None:
    out = helper.sanitize(_agent_config(tmp_path))
    assert "Address = 10.8.0.9/32" in out
    assert "Endpoint = gw.example.com:51820" in out
    assert "AllowedIPs = 10.8.0.1/32" in out


@pytest.mark.parametrize(
    "extra",
    [
        "PostUp = id > /tmp/pwned",
        "PreUp = touch /root/x",
        "PostDown = sh",
        "Table = off",
        "SaveConfig = true",
        "DNS = 1.1.1.1",
    ],
)
def test_hooks_and_other_options_are_refused(tmp_path: Path, extra: str) -> None:
    text = _agent_config(tmp_path).replace("[Interface]\n", f"[Interface]\n{extra}\n")
    with pytest.raises(SystemExit):
        helper.sanitize(text)


def test_bad_values_are_refused(tmp_path: Path) -> None:
    base = _agent_config(tmp_path)
    for bad in (
        base.replace("10.8.0.9/32", "10.8.0.9/32; reboot"),
        base.replace("gw.example.com:51820", "$(id):51820"),
        base + "\n[Peer]\nPublicKey = " + KEY + "\n",
    ):
        with pytest.raises(SystemExit):
            helper.sanitize(bad)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("AllowedIPs = 10.8.0.1/32", "AllowedIPs = 0.0.0.0/0, ::/0"),
        ("AllowedIPs = 10.8.0.1/32", "AllowedIPs = 192.168.1.0/24"),
        ("AllowedIPs = 10.8.0.1/32", "AllowedIPs = 8.8.8.8/32"),
        ("Address = 10.8.0.9/32", "Address = 10.8.0.9/16"),
    ],
)
def test_tunnel_addresses_must_be_single_private_hosts(tmp_path: Path, old: str, new: str) -> None:
    # Otherwise the caller could route the host's (or a LAN's) traffic into a peer it chose.
    with pytest.raises(SystemExit):
        helper.sanitize(_agent_config(tmp_path).replace(old, new))


def test_hosts_on_a_local_network_are_refused(tmp_path: Path, monkeypatch) -> None:
    routes = {
        "10.8.0.9": '[{"dst":"10.8.0.9","gateway":"192.168.1.1","dev":"eth0"}]',
        "10.8.0.1": '[{"dst":"10.8.0.1","gateway":"192.168.1.1","dev":"eth0"}]',
        "192.168.1.1": '[{"dst":"192.168.1.1","dev":"eth0","prefsrc":"192.168.1.5"}]',
    }

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=routes[args[-1]], stderr="")

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    conf = helper.sanitize(_agent_config(tmp_path))
    assert helper.tunnel_hosts(conf) == ["10.8.0.9", "10.8.0.1"]
    helper.refuse_local_hosts(helper.tunnel_hosts(conf))
    with pytest.raises(SystemExit):
        helper.refuse_local_hosts(["192.168.1.1"])


def test_reads_only_regular_files_owned_by_the_caller(tmp_path: Path, monkeypatch) -> None:
    conf = tmp_path / "wg0.conf"
    conf.write_text(_agent_config(tmp_path), encoding="utf-8")
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    assert "PrivateKey" in helper.read_caller_file(str(conf))
    link = tmp_path / "link.conf"
    link.symlink_to(conf)
    with pytest.raises(SystemExit):
        helper.read_caller_file(str(link))
    monkeypatch.setenv("SUDO_UID", str(os.getuid() + 1))
    with pytest.raises(SystemExit):
        helper.read_caller_file(str(conf))


def test_only_fixed_commands_are_accepted(monkeypatch) -> None:
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    calls: list[list[str]] = []
    monkeypatch.setattr(helper, "run", lambda args, check=True: calls.append(args) or 0)
    assert helper.main(["show", "endpoints"]) == 0
    assert helper.main(["set-endpoint", KEY, "1.2.3.4:51820"]) == 0
    assert calls == [
        ["wg", "show", "wg0", "endpoints"],
        ["wg", "set", "wg0", "peer", KEY, "endpoint", "1.2.3.4:51820"],
    ]
    for argv in (["show", "private-key"], ["set-endpoint", "x", "1.2.3.4:1"], ["exec", "sh"], []):
        with pytest.raises(SystemExit):
            helper.main(argv)
