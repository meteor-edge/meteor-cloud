"""Stage 4b: the Go meteorcli itself, on a real device.

The device runs the build of this commit (scripts/dist.sh), compiled to trust only this
environment's release test key (see e2e.source.build_meteorcli). The same archive holds two
releases signed with that key, so the updater is tested end to end without GitHub:

- ``update``: a newer healthy build. The console's Update meteorcli must install it.
- ``broken``: passes the updater's ``--version`` smoke test, then never starts. The updater must
  put the previous binary back and the console must show the action failed.

The suite also checks the agent's privilege split (unprivileged service, root helper, no sudo
for the CLI), its doctor report (on the device and through the console), and enrollment by
approval with an enrollment API key on device 2.
"""

from __future__ import annotations

import json
import shlex

from e2e.redact import redact
from e2e.suite.context import METEORCLI
from e2e.suite.framework import Fail, NotSupported, check, expect, wait_for
from e2e.suite.s4_onboarding import RESET, configure

STAGE = "4b meteorcli agent"
DIST = "/opt/meteorcli-dist"  # deploy/ansible/roles/device: device_install_dir
UPDATES = f"{DIST}/e2e-updates"
# A copy of the update without its signature; outside /tmp and /home, which the updater cannot see.
UNSIGNED = "/opt/meteorcli-e2e-unsigned"
PREVIOUS = f"{METEORCLI}.previous"
# uname -m -> release binary suffix (scripts/build.sh).
ARCH = ('case "$(uname -m)" in x86_64) a=amd64;; aarch64|arm64) a=arm64;; armv6l|armv7l) a=armv6;; '
        '*) echo "unknown arch $(uname -m)" >&2; exit 1;; esac')
ACTION_TIMEOUT = 600  # one heartbeat to collect, up to 3 minutes of health gate, one to report


# -- helpers ----------------------------------------------------------------------------
def versions(ctx) -> dict[str, str]:
    """The versions this run built (e2e.source writes them into the device archive)."""
    if "agent_versions" not in ctx.data:
        raw = ctx.sh(ctx.device(0), f"cat {UPDATES}/versions.json", check=False)
        if not raw.ok:
            raise Fail(f"{UPDATES}/versions.json is missing: the device was not configured from a "
                       "test build with update releases (meteorcloud-testenv up rebuilds it)")
        ctx.data["agent_versions"] = json.loads(raw.stdout)
    return ctx.data["agent_versions"]


def device_row(ctx, index: int = 0) -> dict:
    info = ctx.data["devices"][index]
    return ctx.admin().call("GET", ctx.org_path(f"/devices/{info['id']}"))


def wait_agent_version(ctx, version: str, *, timeout: float = 240) -> dict:
    return wait_for(lambda: (d := device_row(ctx))["agent_version"] == version and d["status"] == "online" and d,
                    timeout=timeout, interval=5, what=f"device 1 online reporting meteorcli {version}")


def binary_sha(ctx, path: str = METEORCLI) -> str:
    return ctx.sh(ctx.device(0), f"sha256sum {path}").stdout.split()[0]


def set_release_url(ctx, url: str) -> None:
    ctx.meteorcli(ctx.device(0), f"config --release-url {shlex.quote(url)}")


def order_action(ctx, kind: str, path: str, *, index: int = 0) -> dict:
    """POST an action and return the one it created."""
    info = ctx.data["devices"][index]
    base = ctx.org_path(f"/devices/{info['id']}/actions")
    api = ctx.admin()
    before = {a["id"] for a in api.call("GET", base)["actions"]}
    created = [a for a in api.call("POST", f"{base}/{path}")["actions"] if a["id"] not in before and a["kind"] == kind]
    expect(len(created) == 1, f"ordering {kind} created {len(created)} actions")
    return created[0]


def wait_action(ctx, action_id: str, *, index: int = 0, timeout: float = ACTION_TIMEOUT) -> dict:
    info = ctx.data["devices"][index]
    base = ctx.org_path(f"/devices/{info['id']}/actions")

    def settled():
        action = next((a for a in ctx.admin().call("GET", base)["actions"] if a["id"] == action_id), None)
        if action is None:
            raise Fail(f"action {action_id} disappeared from the device's actions")
        return action["state"] in ("done", "failed") and action

    return wait_for(settled, timeout=timeout, interval=10, what=f"action {action_id} to finish")


def restore_installed_build(ctx) -> bool:
    """Put this commit's build back when an earlier suite run left the update installed."""
    want = versions(ctx)["installed"]
    if device_row(ctx)["agent_version"] == want:
        return False
    ctx.sh(ctx.device(0), f"{ARCH}; install -m 0755 {DIST}/meteorcli-linux-$a {METEORCLI}; rm -f {PREVIOUS}; "
                          "systemctl restart meteorcli-helper.socket meteorcli")
    wait_agent_version(ctx, want)
    return True


# -- checks -----------------------------------------------------------------------------
@check("agent.build_version", STAGE, "Device 1 runs the meteorcli built from this commit and reports its version",
       needs=("device.online",))
def build_version(ctx):
    want = versions(ctx)
    restored = restore_installed_build(ctx)
    set_release_url(ctx, "default")
    printed = ctx.meteorcli(ctx.device(0), "--version").stdout.strip()
    expect(printed == f"meteorcli {want['installed']}", f"meteorcli --version printed {printed!r}")
    row = wait_agent_version(ctx, want["installed"])
    return {"agent_version": row["agent_version"], "commit": want["commit"], "restored_from_earlier_run": restored}


@check("agent.privilege_split", STAGE, "The agent runs as the unprivileged meteor user; only the helper is root",
       needs=("device.online",))
def privilege_split(ctx):
    device = ctx.device(0)
    pid = ctx.sh(device, "systemctl show -p MainPID --value meteorcli").stdout.strip()
    expect(pid not in ("", "0"), "meteorcli.service has no main process")
    agent_user = ctx.sh(device, f"ps -o user= -p {pid}").stdout.strip()
    expect(agent_user == "meteor", f"meteorcli.service runs as {agent_user!r}, not meteor")
    socket = ctx.sh(device, "systemctl is-active meteorcli-helper.socket", check=False).stdout.strip()
    expect(socket == "active", f"meteorcli-helper.socket is {socket!r}")
    token_owner = ctx.sh(device, "stat -c '%U %a' /var/lib/meteorcli/identity/device-token").stdout.strip()
    expect(token_owner.split()[0] == "meteor" and token_owner.split()[1] in ("600", "400"),
           f"the device credential is '{token_owner}', want meteor 600")
    return {"agent_user": agent_user, "helper_socket": socket, "credential": token_owner}


@check("agent.cli_without_sudo", STAGE, "The SSH user runs meteorcli status and doctor without sudo",
       needs=("device.online",))
def cli_without_sudo(ctx):
    device = ctx.device(0)
    info = ctx.data["devices"][0]
    status = ctx.sh(device, f"{METEORCLI} status", sudo=False, check=False)
    expect(status.ok, f"meteorcli status as the SSH user exited {status.returncode}: {status.text()[-600:]}")
    expect(info["id"] in status.stdout, f"meteorcli status does not show device {info['id']}: {status.stdout[-600:]}")
    expect("Agent:          running" in status.stdout, f"the agent did not answer over its socket: {status.stdout[:400]}")
    doctor = ctx.sh(device, f"{METEORCLI} doctor --json", sudo=False, check=False)
    try:
        report = json.loads(doctor.stdout)
    except json.JSONDecodeError as exc:
        raise Fail(f"meteorcli doctor --json printed no JSON ({exc}): {doctor.text()[-600:]}") from exc
    failed = [c for c in report.get("checks", []) if c.get("level") == "fail"]
    expect(doctor.ok and not failed, f"meteorcli doctor found problems: {failed or doctor.text()[-600:]}")
    sudo_denied = ctx.sh(device, f"{METEORCLI} config --release-url default", sudo=False, check=False)
    expect(not sudo_denied.ok, "meteorcli config (root only) worked without sudo")
    return {"doctor_version": report.get("version"),
            "doctor": {c["name"]: c["level"] for c in report.get("checks", [])}}


@check("agent.diagnostics_action", STAGE, "Run diagnostics from the console returns the device's doctor report",
       needs=("device.online",))
def diagnostics_action(ctx):
    action = wait_action(ctx, order_action(ctx, "run_diagnostics", "run-diagnostics")["id"], timeout=300)
    expect(action["state"] == "done", f"run_diagnostics ended {action['state']}: {action.get('last_error')}")
    output = action.get("output") or ""
    expect(len(output) > 40, f"the diagnostics report is empty: {output!r}")
    return {"state": action["state"], "output_head": output[:600]}


@check("agent.update_refuses_unsigned", STAGE, "The updater refuses a release without a valid signature",
       needs=("agent.build_version",))
def update_refuses_unsigned(ctx):
    want = versions(ctx)
    before = binary_sha(ctx)
    ctx.sh(ctx.device(0), f"rm -rf {UNSIGNED} && cp -r {UPDATES}/update {UNSIGNED} && "
                          f"rm -f {UNSIGNED}/meteorcli-release.json.sig")
    try:
        set_release_url(ctx, f"file://{UNSIGNED}")
        action = wait_action(ctx, order_action(ctx, "update_agent", "update-agent")["id"])
    finally:
        set_release_url(ctx, "default")
        ctx.sh(ctx.device(0), f"rm -rf {UNSIGNED}", check=False)
    expect(action["state"] == "failed", f"an unsigned release ended {action['state']}, want failed")
    expect(binary_sha(ctx) == before, "the installed binary changed")
    row = wait_agent_version(ctx, want["installed"])
    return {"state": action["state"], "last_error": action.get("last_error"), "agent_version": row["agent_version"]}


@check("agent.update_signed", STAGE, "Update meteorcli in the console installs a newer signed release",
       needs=("agent.build_version",))
def update_signed(ctx):
    want = versions(ctx)
    old_sha = binary_sha(ctx)
    set_release_url(ctx, f"file://{UPDATES}/update")
    action = wait_action(ctx, order_action(ctx, "update_agent", "update-agent")["id"])
    expect(action["state"] == "done", f"update_agent ended {action['state']}: {action.get('last_error')}")
    expect(action.get("from_version") == want["installed"], f"from_version {action.get('from_version')!r}")
    row = wait_agent_version(ctx, want["update"])
    printed = ctx.meteorcli(ctx.device(0), "--version").stdout.strip()
    expect(printed == f"meteorcli {want['update']}", f"meteorcli --version printed {printed!r}")
    expect(binary_sha(ctx, PREVIOUS) == old_sha, f"{PREVIOUS} is not the build that was replaced")
    return {"from": want["installed"], "to": row["agent_version"], "previous_kept": True}


@check("agent.update_rollback", STAGE, "A signed release that does not come up is rolled back automatically",
       needs=("agent.update_signed",))
def update_rollback(ctx):
    want = versions(ctx)
    good_sha = binary_sha(ctx)
    set_release_url(ctx, f"file://{UPDATES}/broken")
    try:
        action = wait_action(ctx, order_action(ctx, "update_agent", "update-agent")["id"])
    finally:
        set_release_url(ctx, "default")
    expect(action["state"] == "failed", f"the broken release ended {action['state']}, want failed")
    expect("roll" in (action.get("last_error") or "").lower(),
           f"the action does not say it rolled back: {action.get('last_error')!r}")
    row = wait_agent_version(ctx, want["update"])
    expect(binary_sha(ctx) == good_sha, "the binary after the rollback is not the previous build")
    return {"last_error": action.get("last_error"), "agent_version": row["agent_version"]}


@check("agent.enroll_by_approval", STAGE, "Device 2 enrolls with an enrollment API key after an admin approves it",
       needs=("device.online",))
def enroll_by_approval(ctx):
    if len(ctx.devices) < 2:
        raise NotSupported("needs a second device (E2E_DEVICE_COUNT>=2)")
    device = ctx.device(1)
    api = ctx.admin()
    name = f"e2e-{ctx.run_id}-approved"
    key = api.call("POST", ctx.org_path("/enrollment-keys"), json_body={"name": f"e2e-{ctx.run_id}"}, expect=(201,))
    redact.add(key["api_key"])
    device_id = None
    try:
        ctx.sh(device, RESET)
        ctx.put_secret_file(device, "/root/.meteorcloud-e2e-key", key["api_key"])
        configure(ctx, device)
        ctx.sh(device, f'{METEORCLI} config --api-key "$(cat /root/.meteorcloud-e2e-key)"; '
                       "rm -f /root/.meteorcloud-e2e-key")
        submitted = ctx.meteorcli(device, f"request-token --name {name}")
        expect("submitted" in submitted.stdout, f"request-token printed: {submitted.text()[-400:]}")
        request = wait_for(
            lambda: next((r for r in api.call("GET", ctx.org_path("/enrollment-requests?status=pending"))
                          if r["requested_name"] == name), None),
            timeout=120, what=f"a pending enrollment request named {name}")
        expect(request["enrollment_key_id"] == key["id"], "the request does not name the enrollment key")
        api.call("POST", ctx.org_path(f"/enrollment-requests/{request['id']}/approve"), json_body={"name": name})
        claimed = wait_for(
            lambda: next((r for r in api.call("GET", ctx.org_path("/enrollment-requests?status=approved"))
                          if r["id"] == request["id"] and r["device_id"]), None),
            timeout=300, interval=10, what="the device to claim its approval")
        device_id = claimed["device_id"]
        row = wait_for(lambda: (d := api.call("GET", ctx.org_path(f"/devices/{device_id}")))["status"] == "online"
                       and d, timeout=240, interval=5, what=f"device {name} online")
        status = ctx.meteorcli(device, "status")
        expect(device_id in status.stdout, f"meteorcli status on device 2 does not show {device_id}")
        return {"request_id": request["id"], "device_id": device_id, "status": row["status"],
                "agent_version": row.get("agent_version")}
    finally:
        # Stage 9 onboards device 2 again with a registration token: give the hardware back.
        ctx.sh(device, "systemctl stop meteorcli", check=False)
        if device_id:
            api.call("DELETE", ctx.org_path(f"/devices/{device_id}"), expect=(204,))
        api.call("POST", ctx.org_path(f"/enrollment-keys/{key['id']}/revoke"))
