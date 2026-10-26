"""Stage 7: artifact -> docker release -> deployment with an endpoint -> public HTTPS app."""

from __future__ import annotations

import hashlib
import json

from e2e.config import E2E_DIR
from e2e.http import peer_certificate, request, tcp_open
from e2e.suite.framework import Blocked, Fail, check, expect, wait_for
from e2e.suite.s3_artifacts import upload_artifact

STAGE = "7 application delivery"
CONTAINER = "e2e-app"
APP_PORT = 8080


def payload_matches(body: str, *, run_id: str, device_id: str, version: str) -> dict | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if data.get("app") == "meteorcloud-e2e" and data.get("run_id") == run_id and \
            data.get("device_id") == device_id and data.get("version") == version:
        return data
    return None


def create_release(ctx, *, index: int, version: str, name: str | None = None, container: str = CONTAINER) -> dict:
    info = ctx.data["devices"][index]
    spec = {
        "container_name": container,
        "ports": [f"{APP_PORT}:{APP_PORT}"],
        "env": {"RUN_ID": ctx.run_id, "DEVICE_ID": info["id"], "VERSION": version},
    }
    return ctx.admin().call("POST", ctx.org_path("/releases"), json_body={
        "name": name or f"e2e-app-{ctx.run_id}-d{index + 1}", "version": version, "kind": "docker",
        "artifact_id": ctx.data["image_artifact"]["id"], "spec": spec,
    }, expect=(201,))


def deploy(ctx, *, release_id: str, index: int, hostname: str | None, name: str | None = None) -> dict:
    info = ctx.data["devices"][index]
    body = {"release_id": release_id, "target": {"device_ids": [info["id"]]},
            "name": name or f"e2e-{ctx.run_id}-d{index + 1}"}
    if hostname:
        body["endpoint"] = {"hostname": hostname, "port": APP_PORT, "protocol": "http"}
    api = ctx.admin()
    created = api.call("POST", ctx.org_path("/deployments"), json_body=body, expect=(201,))

    def finished():
        row = api.call("GET", ctx.org_path(f"/deployments/{created['id']}"))
        rows = row.get("devices") or []
        failed = [d for d in rows if d["status"] in ("failed", "cancelled")]
        if failed or row["status"] in ("failed", "cancelled"):
            raise Fail(f"deployment {row['status']}: " + "; ".join(str(d.get("last_error")) for d in failed))
        return row if row["status"] == "completed" and rows and all(d["status"] == "succeeded" for d in rows) \
            else None

    row = wait_for(finished, timeout=420, interval=5, what="the deployment to complete")
    return {"deployment_id": row["id"], "status": row["status"], "counts": row.get("counts")}


def upstream_body(ctx, index: int) -> str:
    address = ctx.data["devices"][index]["wg_address"]
    return ctx.backend_python(
        "import json, urllib.request\n"
        f"print(json.dumps(urllib.request.urlopen('http://{address}:{APP_PORT}/', timeout=10).read().decode()))\n"
    )


def public_body(ctx, hostname: str) -> str | None:
    response = request("GET", f"https://{hostname}/", ca_file=ctx.cfg.ca_file, timeout=15)
    return response.text if response.status == 200 else None


def endpoint_row(ctx, hostname: str) -> dict | None:
    rows = ctx.admin().call("GET", ctx.org_path("/endpoints"))
    return next((r for r in rows if r["hostname"] == hostname), None)


def require_public(ctx) -> None:
    if not ctx.cfg.has_public_dns:
        raise Blocked("no public domain: public app URLs cannot be verified")


@check("app.image_artifact", STAGE, "Test app image is built and uploaded as a docker artifact",
       needs=("api.admin_org", "infra.ssh"))
def image_artifact(ctx):
    remote = f"/tmp/e2e-testapp-{ctx.run_id}"
    ctx.sh(ctx.cp, f"rm -rf {remote} && mkdir -p {remote}", sudo=False)
    for name in ("Dockerfile", "serve.sh"):
        result = ctx.ssh.copy_to(ctx.cp, E2E_DIR / "testapp" / name, f"{remote}/{name}")
        expect(result.ok, f"copy {name}: {result.text()}")
    tag = f"meteorcloud-e2e-testapp:{ctx.run_id}"
    ctx.sh(ctx.cp, f"docker build -q -t {tag} {remote} && docker save -o {remote}/image.tar {tag} && "
                   f"chmod 0644 {remote}/image.tar", timeout=600)
    local = ctx.state.build_dir / f"testapp-{ctx.run_id}.tar"
    result = ctx.ssh.copy_from(ctx.cp, f"{remote}/image.tar", local)
    expect(result.ok, f"fetch image tarball: {result.text()}")
    data = local.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    artifact = upload_artifact(ctx, name=f"e2e-testapp-{ctx.run_id}", version="1", kind="docker",
                               filename="testapp.tar", data=data, content_type="application/x-tar")
    expect(artifact["checksum_sha256"] == digest, "API checksum differs from the tarball")
    ctx.data["image_artifact"] = {"id": artifact["id"], "sha256": digest}
    return {"artifact_id": artifact["id"], "image": tag, "bytes": len(data), "sha256": digest}


@check("app.deploy_v1", STAGE, "Docker release v1 deploys to device 1 with an endpoint",
       needs=("app.image_artifact", "wg.device_address"))
def deploy_v1(ctx):
    release = create_release(ctx, index=0, version="1")
    hostname = ctx.suggested_hostname(0, f"e2e-{ctx.run_id}")
    labels = hostname.split(".")
    expect(labels[1] == ctx.data["devices"][0]["name"] and hostname.endswith(f".{ctx.cfg.apps_base_domain}"),
           f"suggested address {hostname} is not <app>.<device>.<org>.{ctx.cfg.apps_base_domain}")
    ctx.data["host_a"] = hostname
    result = deploy(ctx, release_id=release["id"], index=0, hostname=hostname)
    return {"release_id": release["id"], "hostname": hostname, **result}


@check("app.container_running", STAGE, "The container runs on the device and answers locally",
       needs=("app.deploy_v1",))
def container_running(ctx):
    device = ctx.device(0)
    state = ctx.sh(device, f"docker inspect -f '{{{{.State.Running}}}} {{{{.Config.Image}}}}' {CONTAINER}").stdout
    expect(state.startswith("true"), f"container state: {state}")
    body = ctx.sh(device, f"curl -fsS http://127.0.0.1:{APP_PORT}/").stdout
    info = ctx.data["devices"][0]
    data = payload_matches(body, run_id=ctx.run_id, device_id=info["id"], version="1")
    expect(data, f"unexpected local response: {body[:300]}")
    return {"container": state.strip(), "response": data}


@check("app.upstream_over_wireguard", STAGE, "The control plane reaches the app over WireGuard",
       needs=("app.container_running", "wg.control_plane_reaches_device"))
def upstream(ctx):
    info = ctx.data["devices"][0]
    body = upstream_body(ctx, 0)
    data = payload_matches(body, run_id=ctx.run_id, device_id=info["id"], version="1")
    expect(data, f"unexpected upstream response: {body[:300]}")
    return {"upstream": f"http://{info['wg_address']}:{APP_PORT}", "response": data}


@check("app.endpoint_ready", STAGE, "The endpoint becomes ready with the device's WireGuard upstream",
       needs=("app.deploy_v1",))
def endpoint_ready(ctx):
    hostname = ctx.data["host_a"]
    row = wait_for(lambda: (r := endpoint_row(ctx, hostname)) and r["availability"] == "ready" and r,
                   timeout=180, what=f"endpoint {hostname} ready")
    address = ctx.data["devices"][0]["wg_address"]
    expect(row["upstream_url"] == f"http://{address}:{APP_PORT}", f"upstream {row['upstream_url']}")
    ctx.data["endpoint_a"] = row["id"]
    return {"endpoint_id": row["id"], "public_url": row["public_url"], "upstream_url": row["upstream_url"]}


@check("app.public_https", STAGE, "The app answers on its public HTTPS URL with a trusted wildcard certificate",
       needs=("app.endpoint_ready", "tls.platform_https", "dns.wildcard_record"))
def public_https(ctx):
    require_public(ctx)
    hostname = ctx.data["host_a"]
    info = ctx.data["devices"][0]
    # The wildcard certificate (Route53 DNS-01) is requested when the first app route appears.
    body = wait_for(lambda: (b := public_body(ctx, hostname)) and
                    payload_matches(b, run_id=ctx.run_id, device_id=info["id"], version="1") and b,
                    timeout=900, interval=15, what=f"https://{hostname}/ to serve the device app")
    cert = peer_certificate(hostname, ca_file=ctx.cfg.ca_file)
    return {"url": f"https://{hostname}/", "response": json.loads(body), "certificate": cert}


@check("app.not_exposed_directly", STAGE, "The app port is not reachable on the device's public address",
       needs=("app.container_running",))
def not_exposed(ctx):
    device = ctx.device(0)
    opened = tcp_open(device.address, APP_PORT)
    expect(not opened, f"{device.address}:{APP_PORT} is reachable from the internet")
    return {"probed": f"{device.address}:{APP_PORT}", "open": opened}
