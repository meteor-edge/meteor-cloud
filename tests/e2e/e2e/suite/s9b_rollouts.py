"""Stage 9b: progressive rollouts (phased, pause/resume, failure threshold, rollback, batched, timeout).

Uses devices 3-5 as a small fleet so one Deploy spans several real agents. Every
release is the test app under one container name, so each device answers with
the version it runs.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from e2e.suite.framework import Fail, NotSupported, check, expect, wait_for
from e2e.suite.s4_onboarding import onboard, start_agent_and_wait_online

STAGE = "9b rollouts"
FLEET = (2, 3, 4)
CONTAINER = "e2e-fleet"
HOST_PORT = 8081


def fleet_ids(ctx) -> list[str]:
    return [ctx.data["devices"][index]["id"] for index in FLEET]


def index_of(ctx, device_id: str) -> int:
    return next(index for index in FLEET if ctx.data["devices"][index]["id"] == device_id)


def fleet_release(ctx, version: str, *, command: str | None = None) -> dict:
    spec = {
        "container_name": CONTAINER,
        "ports": [f"{HOST_PORT}:8080"],
        "env": {"RUN_ID": ctx.run_id, "DEVICE_ID": "fleet", "VERSION": version},
    }
    if command:
        spec["command"] = command
    return ctx.admin().call("POST", ctx.org_path("/releases"), json_body={
        "name": f"e2e-fleet-{ctx.run_id}", "version": version, "kind": "docker",
        "artifact_id": ctx.data["image_artifact"]["id"], "spec": spec,
    }, expect=(201,))


def create_deploy(ctx, release: dict, rollout: dict, device_ids: list[str] | None = None) -> dict:
    return ctx.admin().call("POST", ctx.org_path("/deployments"), json_body={
        "release_id": release["id"], "name": f"e2e-{ctx.run_id}-{rollout['strategy']}-v{release['version']}",
        "target": {"device_ids": device_ids or fleet_ids(ctx)}, "rollout": rollout,
    }, expect=(201,))


def detail(ctx, deployment_id: str) -> dict:
    return ctx.admin().call("GET", ctx.org_path(f"/deployments/{deployment_id}"))


def action(ctx, deployment_id: str, name: str, expect_status: tuple[int, ...] = (200, 201)) -> dict:
    return ctx.admin().call("POST", ctx.org_path(f"/deployments/{deployment_id}/{name}"), expect=expect_status)


def rows(deployment: dict) -> dict[str, dict]:
    return {row["device_id"]: row for row in deployment.get("devices") or []}


def wait_status(ctx, deployment_id: str, status: str, *, timeout: float = 600) -> dict:
    def probe():
        row = detail(ctx, deployment_id)
        if row["status"] in ("failed", "cancelled", "completed", "paused") and row["status"] != status:
            raise Fail(f"deployment is {row['status']}, expected {status}: counts {row['counts']}; errors "
                       + "; ".join(str(d.get("last_error")) for d in row.get("devices") or [] if d.get("last_error")))
        return row if row["status"] == status else None

    return wait_for(probe, timeout=timeout, interval=5, what=f"deployment {status}")


def running_version(ctx, index: int) -> str | None:
    result = ctx.sh(ctx.device(index), f"curl -fsS --max-time 5 http://127.0.0.1:{HOST_PORT}/", check=False)
    if not result.ok:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    return data.get("version") if data.get("run_id") == ctx.run_id else None


def wait_versions(ctx, indexes, version: str, *, timeout: float = 180) -> dict[str, str | None]:
    def probe():
        seen = {ctx.data["devices"][i]["name"]: running_version(ctx, i) for i in indexes}
        return seen if all(v == version for v in seen.values()) else None

    return wait_for(probe, timeout=timeout, interval=5, what=f"devices to serve version {version}")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def require_fleet(ctx) -> None:
    if len(ctx.devices) < FLEET[-1] + 1:
        raise NotSupported(f"rollout checks need E2E_DEVICE_COUNT={FLEET[-1] + 1} (have {len(ctx.devices)})")


@check("rollout.fleet_onboard", STAGE, "Devices 3-5 onboard as a fleet for rollout checks",
       needs=("device.online",))
def fleet_onboard(ctx):
    require_fleet(ctx)
    evidence = {}
    for index in FLEET:
        registered = onboard(ctx, index)
        ctx.sh(ctx.device(index), f"docker rm -f {CONTAINER} >/dev/null 2>&1 || true")
        online = start_agent_and_wait_online(ctx, index)
        evidence[registered["name"]] = {**registered, **online}
    return evidence


@check("rollout.phased_soak", STAGE, "Phased: canary first, the rest only after the soak delay",
       needs=("rollout.fleet_onboard", "app.image_artifact"))
def phased_soak(ctx):
    release = fleet_release(ctx, "1")
    created = create_deploy(ctx, release, {
        "strategy": "phased", "phases": [{"count": 1}, {"percent": 100, "delay_seconds": 90}],
        "failure_threshold_percent": 10, "pending_timeout_seconds": 900,
    })
    expect(created["counts"]["pending"] == 1 and created["counts"]["queued"] == 2,
           f"first wave counts {created['counts']}")
    expect(created["rollout"]["state"]["current_phase"] == 0, f"state {created['rollout'].get('state')}")
    canary = created["rollout"]["activated_device_ids"][0]
    others = [index for index in FLEET if ctx.data["devices"][index]["id"] != canary]

    soaking = wait_for(lambda: (d := detail(ctx, created["id"]))["rollout"]["state"].get("phase_completed_at")
                       and d, timeout=420, what="the canary to finish")
    completed_at = parse_time(soaking["rollout"]["state"]["phase_completed_at"])
    expect(rows(soaking)[canary]["status"] == "succeeded", f"canary row {rows(soaking)[canary]}")
    expect(soaking["counts"]["queued"] == 2, f"counts during soak {soaking['counts']}")
    untouched = {ctx.data["devices"][i]["name"]: running_version(ctx, i) for i in others}
    expect(all(v is None for v in untouched.values()), f"queued devices already run the release: {untouched}")

    done = wait_status(ctx, created["id"], "completed", timeout=900)
    second_wave = [parse_time(rows(done)[ctx.data["devices"][i]["id"]]["activated_at"]) for i in others]
    waited = min((t - completed_at).total_seconds() for t in second_wave)
    expect(waited >= 90, f"second phase started {waited:.0f}s after the canary finished (delay 90s)")
    versions = wait_versions(ctx, FLEET, "1")
    ctx.data["fleet_release_v1"] = release["id"]
    return {"deployment_id": created["id"], "canary": ctx.data["devices"][index_of(ctx, canary)]["name"],
            "second_phase_after_s": round(waited), "counts": done["counts"], "versions": versions}


@check("rollout.pause_resume", STAGE, "Pause holds the next phase; Resume finishes the rollout",
       needs=("rollout.phased_soak",))
def pause_resume(ctx):
    release = fleet_release(ctx, "2")
    created = create_deploy(ctx, release, {
        "strategy": "phased", "phases": [{"count": 1}, {"percent": 100, "delay_seconds": 30}],
        "failure_threshold_percent": 10, "pending_timeout_seconds": 900,
    })
    paused = action(ctx, created["id"], "pause")
    expect(paused["status"] == "paused" and paused["rollout"]["state"]["paused_reason"] == "manual",
           f"after pause: {paused['status']} {paused['rollout'].get('state')}")
    canary = created["rollout"]["activated_device_ids"][0]
    wait_for(lambda: rows(detail(ctx, created["id"]))[canary]["status"] == "succeeded", timeout=420,
             what="the canary to finish while paused")
    # Longer than the soak delay plus a sweep interval: a running Deploy would have moved on.
    time.sleep(100)
    held = detail(ctx, created["id"])
    expect(held["status"] == "paused" and held["counts"]["queued"] == 2, f"while paused: {held['counts']}")

    resumed = action(ctx, created["id"], "resume")
    expect(resumed["status"] == "in_progress", f"after resume: {resumed['status']}")
    done = wait_status(ctx, created["id"], "completed", timeout=600)
    versions = wait_versions(ctx, FLEET, "2")
    ctx.data["fleet_release_v2"] = release["id"]
    return {"deployment_id": created["id"], "held_counts": held["counts"], "counts": done["counts"],
            "versions": versions}


@check("rollout.failure_threshold_rollback", STAGE,
       "A failing canary pauses the rollout; rollback restores it and the original cannot resume",
       needs=("rollout.pause_resume",))
def failure_threshold_rollback(ctx):
    # The app container exits at once, so the install fails on the canary.
    release = fleet_release(ctx, "3", command="sh -c 'exit 3'")
    created = create_deploy(ctx, release, {
        "strategy": "phased", "phases": [{"count": 1}, {"percent": 100}],
        "failure_threshold_percent": 10, "pending_timeout_seconds": 900,
    })
    canary = created["rollout"]["activated_device_ids"][0]
    others = [index for index in FLEET if ctx.data["devices"][index]["id"] != canary]
    paused = wait_status(ctx, created["id"], "paused", timeout=420)
    expect(paused["rollout"]["state"]["paused_reason"] == "failure_threshold",
           f"paused reason {paused['rollout']['state']}")
    expect(rows(paused)[canary]["status"] == "failed" and paused["counts"]["queued"] == 2,
           f"counts {paused['counts']}")
    held = {ctx.data["devices"][i]["name"]: running_version(ctx, i) for i in others}
    expect(all(v == "2" for v in held.values()), f"devices outside the canary changed: {held}")

    rollback = action(ctx, created["id"], "rollback", (201,))
    expect(rollback["release_id"] == ctx.data["fleet_release_v2"], f"rollback release {rollback['release_id']}")
    expect(rollback["target"]["device_ids"] == [canary], f"rollback targets {rollback['target']}")
    wait_status(ctx, rollback["id"], "completed", timeout=420)
    canary_index = index_of(ctx, canary)
    wait_versions(ctx, [canary_index], "2")

    refused = {}
    for name in ("resume", "retry-failed"):
        body = action(ctx, created["id"], name, (409,))
        refused[name] = body["error"]["code"]
    expect(set(refused.values()) == {"deployment_rolled_back"}, f"original after rollback: {refused}")

    cancelled = action(ctx, created["id"], "cancel")
    expect(cancelled["status"] == "cancelled", f"cancel: {cancelled['status']}")
    software = ctx.admin().call("GET", ctx.org_path(f"/devices/{canary}/software"))
    desired = {item["workload_key"]: item["desired_release_id"] for item in software}
    expect(ctx.data["fleet_release_v2"] in desired.values(),
           f"cancel overwrote the rollback's desired release: {desired}")
    time.sleep(45)  # a heartbeat later the canary still runs the rolled-back version
    expect(running_version(ctx, canary_index) == "2", "canary left version 2 after cancelling the original")
    return {"deployment_id": created["id"], "rollback_id": rollback["id"], "paused_counts": paused["counts"],
            "refused": refused, "held": held}


@check("rollout.batched", STAGE, "Batched keeps target order and finishes in batches",
       needs=("rollout.fleet_onboard", "app.image_artifact"))
def batched(ctx):
    release = fleet_release(ctx, "4")
    targets = fleet_ids(ctx)
    created = create_deploy(ctx, release, {"strategy": "batched", "batch_size": 2}, targets)
    expect(created["counts"]["pending"] == 2 and created["counts"]["queued"] == 1, f"counts {created['counts']}")
    expect(sorted(created["rollout"]["activated_device_ids"]) == sorted(targets[:2]),
           f"first batch {created['rollout']['activated_device_ids']} is not the first two targets")
    expect("state" not in created["rollout"], f"batched rollout got state {created['rollout'].get('state')}")
    done = wait_status(ctx, created["id"], "completed", timeout=600)
    versions = wait_versions(ctx, FLEET, "4")
    return {"deployment_id": created["id"], "counts": done["counts"], "versions": versions}


@check("rollout.pending_timeout", STAGE,
       "A silent device times out by the background sweep; its late report still records success",
       needs=("rollout.fleet_onboard", "app.image_artifact"))
def pending_timeout(ctx):
    index = FLEET[-1]
    device = ctx.device(index)
    device_id = ctx.data["devices"][index]["id"]
    release = fleet_release(ctx, "5")
    ctx.sh(device, "systemctl stop meteorcli")
    try:
        created = create_deploy(ctx, release, {
            "strategy": "phased", "phases": [{"percent": 100}], "pending_timeout_seconds": 60,
        }, [device_id])
        # No device in this Deploy heartbeats, so only the sweeper can time it out.
        failed = wait_status(ctx, created["id"], "failed", timeout=300)
        row = rows(failed)[device_id]
        expect(row["status"] == "timed_out", f"row {row['status']}")
        expect("No report within 60 seconds" in (row.get("last_error") or ""), f"last_error {row.get('last_error')}")
    finally:
        ctx.sh(device, "systemctl start meteorcli")
    late = wait_for(lambda: (d := detail(ctx, created["id"])) and rows(d)[device_id]["status"] == "succeeded" and d,
                    timeout=420, what="the late report to record success")
    expect(late["status"] == "failed", f"late report reopened the Deploy: {late['status']}")
    wait_versions(ctx, [index], "5")
    return {"deployment_id": created["id"], "timed_out_error": row["last_error"], "final_status": late["status"]}
