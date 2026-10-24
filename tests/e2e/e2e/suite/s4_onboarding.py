"""Stage 4: device onboarding with the real meteorcli and a registration token."""

from __future__ import annotations

import re

from e2e.redact import redact
from e2e.suite.framework import Fail, check, expect, wait_for

STAGE = "4 onboarding"
TOKEN_FILE = "/root/.meteorcloud-e2e-token"
REGISTERED_RE = re.compile(r"Registered device ([0-9a-f-]{36})")

# Start from a clean device: no agent, no tunnel, no credentials, no workload from an earlier run.
RESET = (
    "systemctl stop meteorcli 2>/dev/null || true; "
    "wg-quick down /etc/meteorcli/wireguard/wg0.conf 2>/dev/null || ip link del wg0 2>/dev/null || true; "
    "docker rm -f e2e-app demo-app >/dev/null 2>&1 || true; "
    "find /etc/meteorcli -mindepth 1 -delete 2>/dev/null || true"
)


def create_token(ctx, name: str) -> str:
    created = ctx.admin().call("POST", ctx.org_path("/registration-tokens"),
                               json_body={"name": name, "max_uses": 1}, expect=(201,))
    token = created["token"]
    redact.add(token)
    return token


def onboard(ctx, index: int) -> dict:
    device = ctx.device(index)
    token = create_token(ctx, f"e2e-{ctx.run_id}-{index + 1}")
    ctx.sh(device, RESET)
    ctx.put_secret_file(device, TOKEN_FILE, token)
    name = f"e2e-{ctx.run_id}-device-{index + 1}"
    result = ctx.meteorcli(device, f"register --server {ctx.api_url} --token-file {TOKEN_FILE} --name {name}")
    match = REGISTERED_RE.search(result.stdout)
    if not match:
        raise Fail(f"meteorcli register printed no device id: {result.text()[-500:]}")
    device_id = match.group(1)
    ctx.data.setdefault("devices", {})[index] = {"id": device_id, "name": name, "token": token}
    return {"device_id": device_id, "name": name, "host": device.name}


def start_agent_and_wait_online(ctx, index: int, timeout: float = 180) -> dict:
    device = ctx.device(index)
    info = ctx.data["devices"][index]
    ctx.sh(device, "systemctl enable meteorcli >/dev/null 2>&1; systemctl restart meteorcli")
    api = ctx.admin()
    row = wait_for(lambda: (d := api.call("GET", ctx.org_path(f"/devices/{info['id']}")))["status"] == "online" and d,
                   timeout=timeout, interval=5, what=f"device {info['name']} online")
    return {"status": row["status"], "last_seen_at": row["last_seen_at"], "os": row.get("os_name")}


@check("device.register", STAGE, "meteorcli registers device 1 with a one-time token",
       needs=("api.admin_org", "infra.ssh"))
def register_device(ctx):
    return onboard(ctx, 0)


@check("device.listed", STAGE, "The device appears in its organization", needs=("device.register",))
def device_listed(ctx):
    info = ctx.data["devices"][0]
    page = ctx.admin().call("GET", ctx.org_path("/devices"))
    ids = [d["id"] for d in page["items"]]
    expect(ids == [info["id"]], f"organization devices {ids} != [{info['id']}]")
    return {"devices": ids}


@check("device.token_single_use", STAGE, "A used registration token cannot register another device",
       needs=("device.register",))
def token_single_use(ctx):
    device = ctx.device(0)
    result = ctx.sh(device, f"d=$(mktemp -d) && METEORCLI_CONFIG_DIR=$d /usr/local/bin/meteorcli register "
                            f"--server {ctx.api_url} --token-file {TOKEN_FILE} --name e2e-reuse; rc=$?; "
                            f"rm -rf $d {TOKEN_FILE}; exit $rc", check=False)
    expect(result.returncode != 0, "reusing the token succeeded")
    total = ctx.admin().call("GET", ctx.org_path("/devices"))["total"]
    expect(total == 1, f"organization has {total} devices after the reuse attempt")
    return {"exit_code": result.returncode, "message": result.text()[-300:]}


@check("device.online", STAGE, "meteorcli.service heartbeats and the device goes online",
       needs=("device.register",))
def device_online(ctx):
    return start_agent_and_wait_online(ctx, 0)


@check("device.identity_survives_restart", STAGE, "Restarting the agent keeps the same device identity",
       needs=("device.online",))
def identity_survives_restart(ctx):
    device = ctx.device(0)
    info = ctx.data["devices"][0]
    before = ctx.sh(device, "sha256sum /etc/meteorcli/device-token").stdout.split()[0]
    api = ctx.admin()
    seen = api.call("GET", ctx.org_path(f"/devices/{info['id']}"))["last_seen_at"]
    ctx.sh(device, "systemctl restart meteorcli")
    row = wait_for(lambda: (d := api.call("GET", ctx.org_path(f"/devices/{info['id']}")))["last_seen_at"] != seen
                   and d["status"] == "online" and d, timeout=120, what="a heartbeat after the restart")
    after = ctx.sh(device, "sha256sum /etc/meteorcli/device-token").stdout.split()[0]
    expect(before == after, "the device credential changed across a restart")
    total = api.call("GET", ctx.org_path("/devices"))["total"]
    expect(total == 1, f"restart created another device (total={total})")
    return {"last_seen_at": row["last_seen_at"], "credential_unchanged": True}
