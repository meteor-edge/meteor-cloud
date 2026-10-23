"""Seed a running environment with demo data to explore by hand.

Creates, through the same public API and meteorcli the suite uses:
  - "Demo Organization" and a demo owner account (password in .state/<env>/demo-login.env)
  - every device host registered as demo-device-N, online, with its WireGuard tunnel up
  - the demo-app docker artifact, releases 1.0.0 and 2.0.0, deployed to demo-device-1 at the
    suggested address https://demo-app.demo-device-1.<org>.<env>.<domain>
  - hello 1.0.0 on demo-device-2 at a custom address, https://hello.<env>.<domain>
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets as secrets_mod
from collections.abc import Callable

from e2e.config import E2E_DIR, Config
from e2e.http import request
from e2e.redact import redact
from e2e.ssh import Ssh
from e2e.state import State
from e2e.suite.context import Context
from e2e.suite.framework import Fail, wait_for
from e2e.suite.s3_artifacts import upload_artifact
from e2e.suite.s4_onboarding import (
    REGISTERED_RE,
    RESET,
    TOKEN_FILE,
    start_agent_and_wait_online,
)
from e2e.suite.s5_wireguard import wait_tunnel
from e2e.suite.s7_apps import APP_PORT, create_release, deploy, endpoint_row

ORG_NAME = "Demo Organization"
APP = "demo-app"
HELLO = "hello"


def _write_private(path: os.PathLike, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _register(ctx: Context, index: int) -> dict:
    device = ctx.device(index)
    name = f"demo-device-{index + 1}"
    created = ctx.admin().call("POST", ctx.org_path("/registration-tokens"),
                               json_body={"name": name, "max_uses": 1}, expect=(201,))
    redact.add(created["token"])
    ctx.sh(device, RESET)
    ctx.put_secret_file(device, TOKEN_FILE, created["token"])
    result = ctx.meteorcli(device, f"register --server {ctx.api_url} --token-file {TOKEN_FILE} --name {name}")
    ctx.sh(device, f"rm -f {TOKEN_FILE}")
    match = REGISTERED_RE.search(result.stdout)
    if not match:
        raise Fail(f"meteorcli register printed no device id: {result.text()[-500:]}")
    ctx.data.setdefault("devices", {})[index] = {"id": match.group(1), "name": name}
    start_agent_and_wait_online(ctx, index)
    wait_tunnel(ctx, index)
    return {"name": name, "id": match.group(1), "host": device.address,
            "wg_address": ctx.data["devices"][index]["wg_address"]}


def _upload_image(ctx: Context) -> dict:
    remote = "/tmp/demo-app"
    ctx.sh(ctx.cp, f"rm -rf {remote} && mkdir -p {remote}", sudo=False)
    for name in ("Dockerfile", "serve.sh"):
        result = ctx.ssh.copy_to(ctx.cp, E2E_DIR / "testapp" / name, f"{remote}/{name}")
        if not result.ok:
            raise Fail(f"copy {name}: {result.text()}")
    tag = f"meteorcloud-{APP}:1"
    ctx.sh(ctx.cp, f"docker build -q -t {tag} {remote} && docker save -o {remote}/image.tar {tag} && "
                   f"chmod 0644 {remote}/image.tar", timeout=600)
    local = ctx.state.build_dir / f"{APP}.tar"
    result = ctx.ssh.copy_from(ctx.cp, f"{remote}/image.tar", local)
    if not result.ok:
        raise Fail(f"fetch image tarball: {result.text()}")
    data = local.read_bytes()
    artifact = upload_artifact(ctx, name=APP, version="1", kind="docker", filename=f"{APP}.tar", data=data,
                               content_type="application/x-tar")
    ctx.data["image_artifact"] = {"id": artifact["id"], "sha256": hashlib.sha256(data).hexdigest()}
    return artifact


def seed(cfg: Config, state: State, secrets: dict[str, str], say: Callable[[str], None]) -> int:
    ctx = Context(cfg=cfg, state=state, ssh=Ssh(state.ensure_ssh_key(cfg), state.known_hosts),
                  secrets=secrets, run_id="demo", hosts=state.hosts())
    summary_file = state.dir / "demo.json"
    login_file = state.dir / "demo-login.env"
    api = ctx.admin()

    existing = next((o for o in api.call("GET", "/api/v1/organizations") if o["name"] == ORG_NAME), None)
    if existing and summary_file.is_file():
        say(f"'{ORG_NAME}' is already seeded; nothing to do.")
        _print_summary(json.loads(summary_file.read_text(encoding="utf-8")), say)
        return 0
    if existing:
        say(f"[seed] removing the unfinished '{ORG_NAME}' from an earlier attempt")
        api.call("DELETE", f"/api/v1/organizations/{existing['id']}", expect=(204,))

    say(f"[seed] organization '{ORG_NAME}'")
    org = api.call("POST", "/api/v1/organizations", expect=(201,),
                   json_body={"name": ORG_NAME, "description": "Seeded demo data: devices, releases, a public app"})
    ctx.data["org_id"] = org["id"]

    email = f"demo@{cfg.fqdn or 'example.com'}"
    # The account outlives a deleted organization, and an existing account keeps its password.
    saved = dict(line.split("=", 1) for line in login_file.read_text(encoding="utf-8").splitlines()
                 if "=" in line) if login_file.is_file() else {}
    password = saved.get("DEMO_PASSWORD") or secrets_mod.token_urlsafe(18)
    redact.add(password)
    say(f"[seed] demo account {email} (owner)")
    api.call("POST", ctx.org_path("/members"), expect=(201,),
             json_body={"email": email, "full_name": "Demo User", "password": password, "role": "owner"})
    _write_private(login_file, f"DEMO_EMAIL={email}\nDEMO_PASSWORD={password}\n")

    devices = []
    for index in range(len(ctx.devices)):
        say(f"[seed] registering demo-device-{index + 1} (meteorcli, WireGuard)")
        devices.append(_register(ctx, index))

    say(f"[seed] building and uploading the {APP} image")
    _upload_image(ctx)

    releases = {}
    for version in ("1.0.0", "2.0.0"):
        releases[version] = create_release(ctx, index=0, version=version, name=APP, container=APP)["id"]
    suggested = ctx.suggested_hostname(0, APP)
    say(f"[seed] deploying {APP} 1.0.0 to demo-device-1 at the suggested address {suggested}")
    deploy(ctx, release_id=releases["1.0.0"], index=0, hostname=suggested, name=f"{APP} 1.0.0")
    say(f"[seed] updating {APP} to 2.0.0")
    deploy(ctx, release_id=releases["2.0.0"], index=0, hostname=None, name=f"{APP} 2.0.0")
    apps = [{"name": APP, "version": "2.0.0", "device": "demo-device-1", "address": "suggested",
             "url": f"https://{suggested}/"}]

    if len(ctx.devices) > 1:
        custom = _available_custom_name(ctx, 1, (HELLO, f"{HELLO}-{cfg.env_name}"))
        release = create_release(ctx, index=1, version="1.0.0", name=HELLO, container=HELLO)["id"]
        say(f"[seed] deploying {HELLO} 1.0.0 to demo-device-2 at the custom address {custom}")
        deploy(ctx, release_id=release, index=1, hostname=custom, name=f"{HELLO} 1.0.0")
        apps.append({"name": HELLO, "version": "1.0.0", "device": "demo-device-2", "address": "custom",
                     "url": f"https://{custom}/"})

    for app in apps:
        hostname = app["url"].split("/")[2]
        wait_for(lambda h=hostname: (r := endpoint_row(ctx, h)) and r["availability"] == "ready",
                 timeout=180, what=f"endpoint {hostname} ready")
        app["response"] = "skipped (no public domain)"
        if cfg.has_public_dns:
            say(f"[seed] waiting for {app['url']} (a new certificate can take a few minutes)")
            body = wait_for(lambda u=app["url"]: (r := request("GET", u, ca_file=cfg.ca_file, timeout=15)).status
                            == 200 and r.text, timeout=900, interval=15, what=app["url"])
            app["response"] = json.loads(body)

    summary = {
        "console": ctx.api_url, "organization": ORG_NAME, "demo_login": str(login_file),
        "admin_login": str(state.secrets_file), "devices": devices, "apps": apps,
    }
    summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _print_summary(summary, say)
    return 0


def _available_custom_name(ctx: Context, index: int, names: tuple[str, ...]) -> str:
    """The first custom <name>.<apps domain> the platform reports as available."""
    device_id = ctx.data["devices"][index]["id"]
    for name in names:
        hostname = f"{name}.{ctx.cfg.apps_base_domain}"
        result = ctx.admin().call("GET", ctx.org_path(
            f"/endpoints/hostname-check?device_id={device_id}&hostname={hostname}"))
        if result["available"]:
            return hostname
    raise Fail(f"none of the custom names {names} is available")


def _print_summary(summary: dict, say: Callable[[str], None]) -> None:
    say("\nDemo data is ready")
    say(f"  console        {summary['console']}")
    say(f"  demo login     {summary['demo_login']}  (DEMO_EMAIL / DEMO_PASSWORD)")
    say(f"  admin login    {summary['admin_login']}  (EDGE_PLATFORM_ADMIN_EMAIL / _PASSWORD)")
    say(f"  organization   {summary['organization']}")
    for device in summary["devices"]:
        say(f"  device         {device['name']:<16} public {device['host']:<16} WireGuard {device['wg_address']}")
    for app in summary.get("apps", []):
        say(f"  app            {app['name']} {app['version']} on {app['device']} (port {APP_PORT}, "
            f"{app['address']} address)")
        say(f"                 {app['url']}")
