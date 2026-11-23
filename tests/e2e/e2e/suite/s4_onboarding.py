"""Stage 4: device onboarding with the real meteorcli and a registration token."""

from __future__ import annotations

import re
import shlex

from e2e.http import Api
from e2e.redact import redact
from e2e.suite.context import DEVICE_TOKEN, WG_CONF
from e2e.suite.framework import Fail, check, expect, wait_for

STAGE = "4 onboarding"
TOKEN_FILE = "/root/.meteorcloud-e2e-token"
REGISTERED_RE = re.compile(r"Registered device ([0-9a-f-]{36})")

# Start from a clean device: no tunnel, no credentials, no workload from an earlier run. The
# agent is started again afterwards: `meteorcli register` asks the running agent to enroll.
RESET = (
    "systemctl stop meteorcli 2>/dev/null || true; "
    f"wg-quick down {WG_CONF} 2>/dev/null || ip link del wg0 2>/dev/null || true; "
    "docker rm -f e2e-app demo-app >/dev/null 2>&1 || true; "
    "find /var/lib/meteorcli/identity /var/lib/meteorcli/state /var/lib/meteorcli/artifacts "
    "-mindepth 1 -delete 2>/dev/null || true; "
    "rm -f /etc/meteorcli/config.json; "
    "systemctl start meteorcli-helper.socket meteorcli"
)


def configure(ctx, device) -> None:
    """Point meteorcli at this run's API. RESET removed config.json, and with it the ssh_user the
    installer set: the browser terminal and device logs sign in as that account."""
    # The agent refuses root as the terminal's account (an on-prem device may be reached as root).
    ssh_user = f" --ssh-user {shlex.quote(device.user)}" if device.user and device.user != "root" else ""
    ctx.meteorcli(device, f"config --api-base {ctx.api_url}{ssh_user}")


def register(ctx, device, name: str) -> str:
    """Point meteorcli at this run's API and register with the token in TOKEN_FILE (meteorcli
    removes the file once registered). Returns the device id."""
    configure(ctx, device)
    result = ctx.meteorcli(device, f"register --token-file {TOKEN_FILE} --name {name}")
    match = REGISTERED_RE.search(result.stdout)
    if not match:
        raise Fail(f"meteorcli register printed no device id: {result.text()[-500:]}")
    return match.group(1)


def create_token(ctx, name: str) -> str:
    created = ctx.admin().call("POST", ctx.org_path("/registration-tokens"),
                               json_body={"name": name, "max_uses": 1}, expect=(201,))
    token = created["token"]
    redact.add(token)
    return token


def machine_macs(ctx) -> set[str]:
    """MAC addresses of this run's device machines, read the way the agent reads them."""
    macs: set[str] = set()
    for host in ctx.devices:
        result = ctx.sh(host, "for f in /sys/class/net/*/address; do "
                              "[ \"$f\" = /sys/class/net/lo/address ] || cat \"$f\"; done",
                        sudo=False, check=False)
        if not result.ok:
            continue
        for line in result.stdout.split():
            mac = line.strip().lower()
            if mac and mac != "00:00:00:00:00:00":
                macs.add(mac)
    return macs


def release_earlier_runs(ctx) -> list[str]:
    """Delete devices from earlier runs' organizations that are this run's machines.

    The API matches a device by its MAC addresses across organizations, so the same
    machines cannot register into this run's organization while an earlier run owns them.
    Only devices whose MAC addresses overlap this run's machines are deleted; other devices
    in an ``E2E `` organization are left alone.
    """
    macs = machine_macs(ctx)
    if not macs:
        return []
    api = ctx.admin()
    deleted = []
    for org in api.call("GET", "/api/v1/organizations"):
        if org["id"] == ctx.data["org_id"] or not org["name"].startswith("E2E "):
            continue
        page = api.call("GET", ctx.org_path("/devices?page_size=100", org=org["id"]))
        for device in page["items"]:
            if not macs & {mac.lower() for mac in device.get("mac_addresses") or []}:
                continue
            api.call("DELETE", ctx.org_path(f"/devices/{device['id']}", org=org["id"]), expect=(204,))
            deleted.append(device["name"])
    return deleted


def onboard(ctx, index: int) -> dict:
    device = ctx.device(index)
    token = create_token(ctx, f"e2e-{ctx.run_id}-{index + 1}")
    ctx.sh(device, RESET)
    ctx.put_secret_file(device, TOKEN_FILE, token)
    name = f"e2e-{ctx.run_id}-device-{index + 1}"
    device_id = register(ctx, device, name)
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
    released = release_earlier_runs(ctx)
    return {**onboard(ctx, 0), "released_from_earlier_runs": released}


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
    # The device is enrolled now, so this registers as other hardware straight against the agent API.
    token = ctx.data["devices"][0]["token"]
    body = {"token": token, "name": "e2e-reuse", "serial_number": f"e2e-reuse-{ctx.run_id}",
            "mac_addresses": ["02:00:00:00:e2:e2"]}
    status = Api(ctx.api_url, ctx.cfg.ca_file).status("POST", "/api/v1/agent/register", json_body=body)
    expect(400 <= status < 500 and status != 422, f"reusing the token answered HTTP {status}")
    total = ctx.admin().call("GET", ctx.org_path("/devices"))["total"]
    expect(total == 1, f"organization has {total} devices after the reuse attempt")
    return {"status": status}


@check("device.online", STAGE, "meteorcli.service heartbeats and the device goes online",
       needs=("device.register",))
def device_online(ctx):
    return start_agent_and_wait_online(ctx, 0)


@check("device.identity_survives_restart", STAGE, "Restarting the agent keeps the same device identity",
       needs=("device.online",))
def identity_survives_restart(ctx):
    device = ctx.device(0)
    info = ctx.data["devices"][0]
    before = ctx.sh(device, f"sha256sum {DEVICE_TOKEN}").stdout.split()[0]
    api = ctx.admin()
    seen = api.call("GET", ctx.org_path(f"/devices/{info['id']}"))["last_seen_at"]
    ctx.sh(device, "systemctl restart meteorcli")
    row = wait_for(lambda: (d := api.call("GET", ctx.org_path(f"/devices/{info['id']}")))["last_seen_at"] != seen
                   and d["status"] == "online" and d, timeout=120, what="a heartbeat after the restart")
    after = ctx.sh(device, f"sha256sum {DEVICE_TOKEN}").stdout.split()[0]
    expect(before == after, "the device credential changed across a restart")
    total = api.call("GET", ctx.org_path("/devices"))["total"]
    expect(total == 1, f"restart created another device (total={total})")
    return {"last_seen_at": row["last_seen_at"], "credential_unchanged": True}
