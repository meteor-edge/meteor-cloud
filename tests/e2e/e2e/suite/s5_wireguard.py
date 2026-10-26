"""Stage 5: the WireGuard tunnel between the gateway and device 1."""

from __future__ import annotations

import time

from e2e.suite.framework import Fail, check, expect, wait_for

STAGE = "5 wireguard"
SERVER_ADDRESS = "10.8.0.1"


def connectivity(ctx, index: int) -> dict:
    info = ctx.data["devices"][index]
    return ctx.admin().call("GET", ctx.org_path(f"/devices/{info['id']}/connectivity"))


def wg_address(ctx, index: int) -> str:
    return ctx.data["devices"][index]["wg_address"]


def handshake_age(ctx, host, public_key: str | None = None) -> float | None:
    out = ctx.sh(host, "wg show wg0 latest-handshakes", check=False).stdout
    for line in out.splitlines():
        key, _, ts = line.partition("\t")
        if (public_key is None or key == public_key) and ts.strip().isdigit() and int(ts) > 0:
            return time.time() - int(ts)
    return None


def gateway_can_reach(ctx, address: str, port: int = 22) -> bool:
    return ctx.sh(ctx.gw, f"timeout 5 bash -c '</dev/tcp/{address}/{port}'", check=False).ok


def wait_tunnel(ctx, index: int, timeout: float = 180) -> dict:
    """Connectivity registered, wg0 up on the device, and a handshake."""
    device = ctx.device(index)
    row = wait_for(lambda: (c := connectivity(ctx, index)).get("wg_address") and c["status"] == "active" and c,
                   timeout=timeout, what="an active WireGuard registration")
    address = row["wg_address"].split("/")[0]
    ctx.data["devices"][index].update(wg_address=address, wg_public_key=row["wg_public_key"])
    wait_for(lambda: address in ctx.sh(device, "ip -4 -o addr show dev wg0", check=False).stdout,
             timeout=120, what=f"wg0 with {address} on the device")
    age = wait_for(lambda: (a := handshake_age(ctx, device)) is not None and a < 180 and a + 0.001,
                   timeout=120, what="a WireGuard handshake on the device")
    return {"wg_address": address, "public_key": row["wg_public_key"], "handshake_age_s": round(age)}


@check("wg.device_address", STAGE, "Device gets its WireGuard address and a fresh handshake",
       needs=("device.online",))
def device_address(ctx):
    return wait_tunnel(ctx, 0)


@check("wg.route", STAGE, "Traffic to the gateway uses wg0 on the device", needs=("wg.device_address",))
def route(ctx):
    out = ctx.sh(ctx.device(0), f"ip route get {SERVER_ADDRESS}").stdout.strip()
    expect("dev wg0" in out, f"route to {SERVER_ADDRESS}: {out}")
    return {"route": out}


@check("wg.handshake_reported", STAGE, "The gateway agent reports the handshake to the API",
       needs=("wg.device_address",))
def handshake_reported(ctx):
    row = wait_for(lambda: (c := connectivity(ctx, 0)).get("last_handshake_at") and c, timeout=120,
                   what="last_handshake_at in the API")
    return {"last_handshake_at": row["last_handshake_at"]}


@check("wg.counters", STAGE, "Tunnel byte counters grow with traffic", needs=("wg.device_address",))
def counters(ctx):
    device = ctx.device(0)

    def totals() -> tuple[int, int]:
        out = ctx.sh(device, "wg show wg0 transfer").stdout.split()
        return int(out[1]), int(out[2])

    rx1, tx1 = totals()
    ctx.sh(ctx.gw, f"ping -c 5 -i 0.3 -W 2 {wg_address(ctx, 0)}")
    rx2, tx2 = totals()
    expect(rx2 > rx1 and tx2 > tx1, f"counters did not grow: rx {rx1}->{rx2}, tx {tx1}->{tx2}")
    return {"rx": [rx1, rx2], "tx": [tx1, tx2]}


@check("wg.control_plane_reaches_device", STAGE, "The API container reaches the device over the tunnel",
       needs=("wg.device_address",))
def control_plane_reaches_device(ctx):
    address = wg_address(ctx, 0)
    result = ctx.backend_python(
        "import json, socket\n"
        f"s = socket.create_connection(({address!r}, 22), timeout=10)\n"
        "banner = s.recv(64).decode(errors='replace').strip()\n"
        "print(json.dumps({'ssh_banner': banner}))\n"
    )
    expect(result["ssh_banner"].startswith("SSH-"), f"no SSH banner from {address}: {result}")
    return {"target": f"{address}:22", **result}


@check("wg.gateway_restart_recovers", STAGE, "After a gateway restart the peer is re-added and reconnects",
       needs=("wg.control_plane_reaches_device",))
def gateway_restart(ctx):
    device_key = ctx.data["devices"][0]["wg_public_key"]
    restarted = time.time()
    ctx.sh(ctx.gw, "systemctl restart wg-quick@wg0")
    wait_for(lambda: device_key in ctx.sh(ctx.gw, "wg show wg0 peers").stdout, timeout=90,
             what="the gateway agent to re-add the device peer")
    wait_for(lambda: gateway_can_reach(ctx, wg_address(ctx, 0)), timeout=120, what="the device to answer again")
    age = handshake_age(ctx, ctx.gw, device_key)
    expect(age is not None and age <= time.time() - restarted + 1, f"no new handshake since the restart (age={age})")
    return {"recovered_after_s": round(time.time() - restarted), "handshake_age_s": round(age or 0)}


@check("wg.wrong_server_key_rejected", STAGE, "A device configured with the wrong server key gets no tunnel",
       needs=("wg.gateway_restart_recovers",))
def wrong_server_key(ctx):
    device = ctx.device(0)
    address = wg_address(ctx, 0)
    peer = ctx.sh(device, "wg show wg0 endpoints").stdout.split()
    server_key, endpoint = peer[0], peer[1]
    ctx.sh(device, "systemctl stop meteorcli")  # keep the agent from repairing wg0 during the check
    try:
        ctx.sh(device, f"bogus=$(wg genkey | wg pubkey) && wg set wg0 peer {server_key} remove && "
                       f"wg set wg0 peer $bogus endpoint {endpoint} allowed-ips {SERVER_ADDRESS}/32 "
                       "persistent-keepalive 5")
        time.sleep(20)
        reachable = gateway_can_reach(ctx, address)
        bogus_handshake = handshake_age(ctx, device)
    finally:
        ctx.sh(device, "wg-quick down /etc/meteorcli/wireguard/wg0.conf || true; "
                       "wg-quick up /etc/meteorcli/wireguard/wg0.conf; systemctl start meteorcli")
    expect(not reachable, "the gateway reached the device with a wrong server key")
    expect(bogus_handshake is None, "a handshake completed with a wrong server key")
    wait_for(lambda: gateway_can_reach(ctx, address), timeout=120, what="the tunnel to recover with the right key")
    return {"reachable_with_wrong_key": reachable, "recovered": True}


@check("wg.device_cannot_reach_gateway", STAGE, "The device cannot open connections to the gateway or API",
       needs=("wg.device_address",))
def device_isolation(ctx):
    device = ctx.device(0)
    attempts = {}
    for port in (22, 80, 443, 8000):
        attempts[f"{SERVER_ADDRESS}:{port}"] = ctx.sh(
            device, f"timeout 5 bash -c '</dev/tcp/{SERVER_ADDRESS}/{port}'", check=False).ok
    opened = [target for target, ok in attempts.items() if ok]
    if opened:
        raise Fail(f"device opened connections to {opened}")
    return {"attempts": attempts}
