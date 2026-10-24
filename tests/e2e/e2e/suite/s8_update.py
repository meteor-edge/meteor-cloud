"""Stage 8: updating the deployed application in place."""

from __future__ import annotations

from e2e.suite.framework import check, expect, wait_for
from e2e.suite.s7_apps import (
    create_release,
    deploy,
    endpoint_row,
    payload_matches,
    public_body,
    require_public,
    upstream_body,
)

STAGE = "8 update"


@check("app.update_v2", STAGE, "Release v2 replaces v1 on device 1", needs=("app.upstream_over_wireguard",))
def update_v2(ctx):
    release = create_release(ctx, index=0, version="2")
    result = deploy(ctx, release_id=release["id"], index=0, hostname=None)
    info = ctx.data["devices"][0]
    body = wait_for(lambda: (b := upstream_body(ctx, 0)) and
                    payload_matches(b, run_id=ctx.run_id, device_id=info["id"], version="2") and b,
                    timeout=120, what="the upstream to serve version 2")
    return {"release_id": release["id"], "response": body.strip(), **result}


@check("app.update_public", STAGE, "The same public URL serves v2 and the endpoint stays ready",
       needs=("app.update_v2", "app.public_https"))
def update_public(ctx):
    require_public(ctx)
    hostname = ctx.data["host_a"]
    info = ctx.data["devices"][0]
    body = wait_for(lambda: (b := public_body(ctx, hostname)) and
                    payload_matches(b, run_id=ctx.run_id, device_id=info["id"], version="2") and b,
                    timeout=180, what=f"https://{hostname}/ to serve version 2")
    row = endpoint_row(ctx, hostname)
    expect(row and row["availability"] == "ready", f"endpoint after update: {row}")
    return {"response": body.strip(), "availability": row["availability"]}
