"""Stage 9: a second device, routing without proxy edits, isolation, tenancy, and revocation."""

from __future__ import annotations

import secrets
import time
import uuid

from e2e.http import Api
from e2e.redact import redact
from e2e.suite.framework import Fail, check, expect, wait_for
from e2e.suite.s4_onboarding import onboard, start_agent_and_wait_online
from e2e.suite.s5_wireguard import connectivity, wait_tunnel
from e2e.suite.s7_apps import (
    create_release,
    deploy,
    endpoint_row,
    payload_matches,
    public_body,
    require_public,
    upstream_body,
)

STAGE = "9 fleet and tenancy"


@check("device2.onboard", STAGE, "Device 2 onboards with the same workflow and gets its own tunnel",
       needs=("device.online", "wg.device_address"))
def onboard_second(ctx):
    registered = onboard(ctx, 1)
    online = start_agent_and_wait_online(ctx, 1)
    tunnel = wait_tunnel(ctx, 1)
    first = ctx.data["devices"][0]
    expect(registered["device_id"] != first["id"], "device 2 got device 1's id")
    expect(tunnel["wg_address"] != first["wg_address"], "device 2 got device 1's WireGuard address")
    return {**registered, **online, **tunnel}


@check("device2.deploy", STAGE, "Device 2 runs its own release behind a second hostname",
       needs=("device2.onboard", "app.image_artifact"))
def deploy_second(ctx):
    release = create_release(ctx, index=1, version="1")
    hostname = ctx.app_hostname("b")
    ctx.data["host_b"] = hostname
    result = deploy(ctx, release_id=release["id"], index=1, hostname=hostname)
    info = ctx.data["devices"][1]
    body = upstream_body(ctx, 1)
    expect(payload_matches(body, run_id=ctx.run_id, device_id=info["id"], version="1"), f"device 2 app: {body}")
    wait_for(lambda: (r := endpoint_row(ctx, hostname)) and r["availability"] == "ready", timeout=180,
             what=f"endpoint {hostname} ready")
    return {"hostname": hostname, **result}


@check("route.two_devices_public", STAGE, "Both hostnames route to their own device with no proxy changes",
       needs=("device2.deploy", "app.update_public"))
def two_routes(ctx):
    require_public(ctx)
    traefik_before = ctx.sh(ctx.cp, "md5sum /opt/edge-platform/compose/traefik/dynamic.yml").stdout.split()[0]
    first, second = ctx.data["devices"][0], ctx.data["devices"][1]
    body_b = wait_for(lambda: (b := public_body(ctx, ctx.data["host_b"])) and
                      payload_matches(b, run_id=ctx.run_id, device_id=second["id"], version="1") and b,
                      timeout=300, interval=10, what="the second hostname to serve device 2")
    body_a = public_body(ctx, ctx.data["host_a"])
    expect(body_a and payload_matches(body_a, run_id=ctx.run_id, device_id=first["id"], version="2"),
           f"first hostname now serves {body_a!r}")
    traefik_after = ctx.sh(ctx.cp, "md5sum /opt/edge-platform/compose/traefik/dynamic.yml").stdout.split()[0]
    expect(traefik_before == traefik_after, "the static proxy configuration changed")
    return {"a": body_a.strip(), "b": body_b.strip(), "static_proxy_config_unchanged": True}


@check("wg.devices_isolated", STAGE, "Devices cannot reach each other over the VPN", needs=("device2.onboard",))
def devices_isolated(ctx):
    results = {}
    for src, dst in ((0, 1), (1, 0)):
        address = ctx.data["devices"][dst]["wg_address"]
        for probe, command in (("tcp22", f"timeout 5 bash -c '</dev/tcp/{address}/22'"),
                               ("tcp8080", f"timeout 5 bash -c '</dev/tcp/{address}/8080'"),
                               ("ping", f"ping -c 2 -W 2 {address}")):
            results[f"device{src + 1}->device{dst + 1}:{probe}"] = ctx.sh(ctx.device(src), command, check=False).ok
    opened = [k for k, ok in results.items() if ok]
    expect(not opened, f"device-to-device traffic allowed: {opened}")
    return {"attempts": results}


@check("tenancy.isolation", STAGE, "Another organization cannot see or use this organization's resources",
       needs=("device.register", "artifact.upload"))
def tenancy(ctx):
    outsider = Api(ctx.api_url, ctx.cfg.ca_file)
    email = f"e2e-outsider-{ctx.run_id}@{ctx.cfg.fqdn or 'example.com'}"
    password = secrets.token_urlsafe(18)
    redact.add(password)
    outsider.call("POST", "/api/v1/auth/register",
                  json_body={"email": email, "full_name": "E2E outsider", "password": password}, expect=(201,))
    redact.add(outsider.login(email, password))
    org_b = outsider.call("POST", "/api/v1/organizations", json_body={"name": f"E2E {ctx.run_id} B"},
                          expect=(201,))["id"]
    device_a = ctx.data["devices"][0]["id"]
    blob = ctx.data["blob"]["id"]
    org_a = ctx.data["org_id"]
    attempts = {
        "list org A devices": outsider.status("GET", ctx.org_path("/devices")),
        "read org A device": outsider.status("GET", ctx.org_path(f"/devices/{device_a}")),
        "read org A device via org B": outsider.status("GET", ctx.org_path(f"/devices/{device_a}", org=org_b)),
        "list org A artifacts": outsider.status("GET", ctx.org_path("/artifacts")),
        "download-link org A artifact": outsider.status("POST", ctx.org_path(f"/artifacts/{blob}/download-link")),
        "shell on org A device": outsider.status("POST", ctx.org_path(f"/devices/{device_a}/shell-sessions")),
        "shell on org A device via org B": outsider.status(
            "POST", ctx.org_path(f"/devices/{device_a}/shell-sessions", org=org_b)),
        "endpoint in org B on org A device": outsider.status(
            "POST", ctx.org_path("/endpoints", org=org_b),
            json_body={"device_id": device_a, "hostname": ctx.app_hostname("steal"), "port": 8080}),
        "revoke org A device": outsider.status("POST", ctx.org_path(f"/devices/{device_a}/connectivity/revoke")),
    }
    allowed = {k: v for k, v in attempts.items() if v < 400}
    expect(not allowed, f"cross-organization access allowed: {allowed}")
    visible = [o["id"] for o in outsider.call("GET", "/api/v1/organizations")]
    expect(org_a not in visible, "organization A is visible to the outsider")
    # The device in org A is untouched.
    expect(connectivity(ctx, 0)["status"] == "active", "device 1 connectivity changed")
    return {"attempts": attempts, "outsider_orgs": len(visible), "probe_id": str(uuid.uuid4())[:8]}


@check("device2.revoke", STAGE, "Revoking device 2 removes its peer and takes its route offline",
       needs=("device2.deploy",))
def revoke_second(ctx):
    info = ctx.data["devices"][1]
    api = ctx.admin()
    started = time.time()
    row = api.call("POST", ctx.org_path(f"/devices/{info['id']}/connectivity/revoke"))
    expect(row["status"] == "revoked", f"connectivity after revoke: {row['status']}")
    wait_for(lambda: info["wg_public_key"] not in ctx.sh(ctx.gw, "wg show wg0 peers").stdout, timeout=90,
             what="the gateway to drop device 2's peer")
    peer_removed_s = round(time.time() - started)
    endpoint = wait_for(lambda: (r := endpoint_row(ctx, ctx.data["host_b"])) and r["availability"] != "ready" and r,
                        timeout=180, what="device 2's endpoint to leave ready")
    evidence = {"peer_removed_after_s": peer_removed_s, "endpoint_availability": endpoint["availability"]}
    if ctx.cfg.has_public_dns:
        wait_for(lambda: not payload_matches(public_body(ctx, ctx.data["host_b"]) or "", run_id=ctx.run_id,
                                             device_id=info["id"], version="1"),
                 timeout=120, what="the public hostname to stop serving device 2")
        evidence["public_route_removed"] = True
    ping = ctx.sh(ctx.gw, f"ping -c 2 -W 2 {info['wg_address']}", check=False)
    if ping.ok:
        raise Fail("the gateway still reaches device 2 after revocation")
    return evidence
