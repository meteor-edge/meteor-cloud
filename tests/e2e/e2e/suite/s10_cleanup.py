"""Stage 10: removing an application route; workload removal."""

from __future__ import annotations

import json

from e2e.http import request
from e2e.suite.framework import NotSupported, check, expect, wait_for
from e2e.suite.s7_apps import CONTAINER, payload_matches, public_body

STAGE = "10 cleanup"


def routed_hostnames(ctx) -> set[str]:
    """Hostnames in Traefik's dynamic configuration, read from the API container (not public)."""
    data = ctx.backend_python(
        "import json, os, urllib.request\n"
        "req = urllib.request.Request('http://127.0.0.1:8000/internal/routing/traefik',"
        " headers={'X-Routing-Internal-Token': os.environ.get('ROUTING_INTERNAL_TOKEN')"
        " or os.environ['CONNECTIVITY_INTERNAL_TOKEN']})\n"
        "doc = json.loads(urllib.request.urlopen(req, timeout=10).read())\n"
        "print(json.dumps([r['rule'] for r in doc['http']['routers'].values()]))\n"
    )
    return {rule.split("`")[1] for rule in data if "`" in rule}


@check("app.endpoint_delete", STAGE, "Deleting the endpoint removes the public route",
       needs=("app.endpoint_ready",))
def endpoint_delete(ctx):
    hostname = ctx.data["host_a"]
    expect(hostname in routed_hostnames(ctx), f"{hostname} was not in the routing table before deletion")
    ctx.admin().call("DELETE", ctx.org_path(f"/endpoints/{ctx.data['endpoint_a']}"), expect=(204,))
    wait_for(lambda: hostname not in routed_hostnames(ctx), timeout=60, what="the route to leave the routing table")
    evidence = {"routing_table": "removed"}
    if ctx.cfg.has_public_dns:
        info = ctx.data["devices"][0]

        def gone():
            body = public_body(ctx, hostname) or ""
            return not payload_matches(body, run_id=ctx.run_id, device_id=info["id"], version="2")

        # Traefik polls the routing table every 5s.
        wait_for(gone, timeout=60, what=f"https://{hostname}/ to stop reaching the device")
        response = request("GET", f"https://{hostname}/", ca_file=ctx.cfg.ca_file, timeout=15)
        evidence["public_after_delete"] = {"status": response.status,
                                           "content_type": response.headers.get("content-type", "")}
    return evidence


@check("app.undeploy", STAGE, "Removing a deployed workload from a device", needs=("app.deploy_v1",))
def undeploy(ctx):
    # The API's own route table (works whether or not /openapi.json is enabled in production).
    routes = ctx.backend_python(
        "import json\n"
        "from app.main import app\n"
        "print(json.dumps([[r.path, sorted(getattr(r, 'methods', None) or [])] for r in app.routes]))\n"
    )
    removal = [f"{method} {path}" for path, methods in routes for method in methods
               if (method == "DELETE" and ("/software" in path or "/deployments" in path))
               or any(word in path for word in ("undeploy", "uninstall"))]
    running = ctx.sh(ctx.device(0), f"docker inspect -f '{{{{.State.Running}}}}' {CONTAINER}", check=False).stdout
    if not removal:
        raise NotSupported("the API has no operation that removes a workload from a device "
                           "(deployments support create/cancel/retry-failed/rollback); "
                           f"the container is still running={running.strip()}")
    expect(False, f"removal operations exist but are not exercised yet: {json.dumps(removal)}")
