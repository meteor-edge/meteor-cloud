"""Stage 9c: rollouts of systemd and docker compose apps, phase sizing, max_concurrent, concurrent sweeps.

Runs on the stage 9b fleet (devices 3-5).
"""

from __future__ import annotations

import time

from e2e.suite.framework import check, expect, wait_for
from e2e.suite.s3_artifacts import upload_artifact
from e2e.suite.s9b_rollouts import (
    FLEET,
    create_deploy,
    detail,
    fleet_ids,
    fleet_release,
    parse_time,
    rows,
    wait_status,
    wait_versions,
)

STAGE = "9c rollout kinds"
UNIT = "e2e-fleet-unit.service"
UNIT_VERSION_FILE = "/run/e2e-fleet-unit.version"
COMPOSE_PROJECT = "e2e-fleet-compose"
COMPOSE_PORT = 8082

UNIT_TEMPLATE = """[Unit]
Description=MeteorCloud E2E fleet unit {version}

[Service]
ExecStart=/bin/sh -c 'echo {run_id}-{version} > {version_file}; exec sleep infinity'
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

COMPOSE_FILE = """services:
  web:
    image: busybox:1.36.1
    command: ["sh", "-c", "mkdir -p /www && echo \\"$$RUN_ID-$$VERSION\\" > /www/index.html && exec httpd -f -p 8080 -h /www"]
    environment:
      RUN_ID: ${{RUN_ID}}
      VERSION: ${{VERSION}}
    ports:
      - "{port}:8080"
    restart: unless-stopped
"""


def activation_times(deployment: dict) -> list:
    return sorted(parse_time(row["activated_at"]) for row in rows(deployment).values())


def systemd_release(ctx, version: str) -> dict:
    unit = UNIT_TEMPLATE.format(version=version, run_id=ctx.run_id, version_file=UNIT_VERSION_FILE)
    artifact = upload_artifact(ctx, name=f"e2e-fleet-unit-{ctx.run_id}", version=version, kind="systemd",
                               filename=UNIT, data=unit.encode(), content_type="text/plain")
    return ctx.admin().call("POST", ctx.org_path("/releases"), json_body={
        "name": f"e2e-fleet-unit-{ctx.run_id}", "version": version, "kind": "systemd",
        "artifact_id": artifact["id"], "spec": {"unit_name": UNIT},
    }, expect=(201,))


def compose_release(ctx, version: str) -> dict:
    artifact = upload_artifact(ctx, name=f"e2e-fleet-compose-{ctx.run_id}", version=version,
                               kind="docker_compose", filename="compose.yml",
                               data=COMPOSE_FILE.format(port=COMPOSE_PORT).encode(), content_type="text/yaml")
    return ctx.admin().call("POST", ctx.org_path("/releases"), json_body={
        "name": f"e2e-fleet-compose-{ctx.run_id}", "version": version, "kind": "docker_compose",
        "artifact_id": artifact["id"],
        "spec": {"project_name": COMPOSE_PROJECT, "env": {"RUN_ID": ctx.run_id, "VERSION": version}},
    }, expect=(201,))


def unit_state(ctx, index: int) -> str:
    result = ctx.sh(ctx.device(index), f"systemctl is-active {UNIT}; cat {UNIT_VERSION_FILE} 2>/dev/null",
                    check=False)
    return " ".join(result.stdout.split())


def compose_body(ctx, index: int) -> str:
    result = ctx.sh(ctx.device(index), f"curl -fsS --max-time 5 http://127.0.0.1:{COMPOSE_PORT}/", check=False)
    return result.stdout.strip() if result.ok else ""


@check("rollout.systemd_percent_waves", STAGE,
       "A systemd unit rolls out in three percent phases (10% / 50% / 100% of 3 = 1, 2, 3 devices)",
       needs=("rollout.fleet_onboard",))
def systemd_percent_waves(ctx):
    release = systemd_release(ctx, "1")
    created = create_deploy(ctx, release, {
        "strategy": "phased", "phases": [{"percent": 10}, {"percent": 50}, {"percent": 100}],
        "pending_timeout_seconds": 900,
    })
    expect(created["counts"]["pending"] == 1 and created["counts"]["queued"] == 2,
           f"first wave {created['counts']}")
    done = wait_status(ctx, created["id"], "completed", timeout=900)
    expect(done["rollout"]["state"]["current_phase"] == 2, f"state {done['rollout']['state']}")
    times = activation_times(done)
    expect(times[0] < times[1] < times[2], f"devices were not activated one wave at a time: {times}")
    expected = f"active {ctx.run_id}-1"
    units = wait_for(lambda: (s := {ctx.data["devices"][i]["name"]: unit_state(ctx, i) for i in FLEET})
                     and all(v == expected for v in s.values()) and s,
                     timeout=120, what=f"{UNIT} active with version 1 on every device")
    return {"deployment_id": created["id"], "activated_at": [t.isoformat() for t in times], "units": units}


@check("rollout.systemd_update", STAGE, "Updating the systemd unit with a phased canary replaces it everywhere",
       needs=("rollout.systemd_percent_waves",))
def systemd_update(ctx):
    release = systemd_release(ctx, "2")
    created = create_deploy(ctx, release, {
        "strategy": "phased", "phases": [{"count": 1}, {"percent": 100, "delay_seconds": 20}],
        "failure_threshold_percent": 0, "pending_timeout_seconds": 900,
    })
    done = wait_status(ctx, created["id"], "completed", timeout=600)
    expected = f"active {ctx.run_id}-2"
    units = wait_for(lambda: (s := {ctx.data["devices"][i]["name"]: unit_state(ctx, i) for i in FLEET})
                     and all(v == expected for v in s.values()) and s,
                     timeout=120, what=f"{UNIT} version 2 on every device")
    return {"deployment_id": created["id"], "counts": done["counts"], "units": units}


@check("rollout.max_concurrent", STAGE, "max_concurrent 1 installs one device at a time",
       needs=("rollout.fleet_onboard", "app.image_artifact"))
def max_concurrent(ctx):
    release = fleet_release(ctx, "6")
    created = create_deploy(ctx, release, {
        "strategy": "phased", "phases": [{"percent": 100}], "max_concurrent": 1, "pending_timeout_seconds": 900,
    })
    expect(created["counts"]["pending"] == 1 and created["counts"]["queued"] == 2, f"counts {created['counts']}")
    peak = 0
    deadline = time.monotonic() + 900
    while True:
        row = detail(ctx, created["id"])
        in_flight = row["counts"]["pending"] + row["counts"]["updating"]
        peak = max(peak, in_flight)
        expect(in_flight <= 1, f"{in_flight} devices installing at once: {row['counts']}")
        if row["status"] != "in_progress":
            break
        expect(time.monotonic() < deadline, f"still in progress: {row['counts']}")
        time.sleep(3)
    expect(row["status"] == "completed", f"deployment {row['status']}: {row['counts']}")
    times = activation_times(row)
    expect(times[0] < times[1] < times[2], f"activations overlapped: {times}")
    versions = wait_versions(ctx, FLEET, "6")
    return {"deployment_id": created["id"], "peak_in_flight": peak,
            "activated_at": [t.isoformat() for t in times], "versions": versions}


@check("rollout.compose_empty_phase_skipped", STAGE,
       "A compose app rolls out; a phase that adds no devices is skipped with its 30-minute delay",
       needs=("rollout.fleet_onboard",))
def compose_empty_phase(ctx):
    release = compose_release(ctx, "1")
    started = time.monotonic()
    # 10% of 3 devices is 1, the canary's size: that phase and its 1800 s delay are skipped.
    created = create_deploy(ctx, release, {
        "strategy": "phased",
        "phases": [{"count": 1}, {"percent": 10, "delay_seconds": 1800}, {"percent": 100, "delay_seconds": 20}],
        "pending_timeout_seconds": 900,
    })
    done = wait_status(ctx, created["id"], "completed", timeout=900)
    elapsed = time.monotonic() - started
    expect(elapsed < 1800, f"took {elapsed:.0f}s: the empty phase's delay was not skipped")
    expect(done["rollout"]["state"]["current_phase"] == 2, f"state {done['rollout']['state']}")
    expected = f"{ctx.run_id}-1"
    bodies = wait_for(lambda: (s := {ctx.data["devices"][i]["name"]: compose_body(ctx, i) for i in FLEET})
                      and all(v == expected for v in s.values()) and s,
                      timeout=180, what="the compose app on every device")
    return {"deployment_id": created["id"], "elapsed_s": round(elapsed), "responses": bodies}


@check("rollout.concurrent_sweeps", STAGE, "Only one sweep runs at a time; the next one ticks Deploys again",
       needs=("rollout.fleet_onboard", "app.image_artifact"))
def concurrent_sweeps(ctx):
    release = fleet_release(ctx, "7")
    # Stays in progress during the soak, so a sweep has something to tick.
    created = create_deploy(ctx, release, {
        "strategy": "phased", "phases": [{"count": 1}, {"percent": 100, "delay_seconds": 3600}],
    }, fleet_ids(ctx))
    try:
        result = ctx.backend_python(
            "import json, threading, time\n"
            "from sqlalchemy import text\n"
            "from app.core.database import engine\n"
            "from app.deployments.sweeper import SWEEP_LOCK_KEY, _sweep_with_new_session\n"
            "with engine.connect() as other:\n"
            "    for _ in range(30):  # the API's own sweeper may hold the lock for a moment\n"
            "        held = other.scalar(text('SELECT pg_try_advisory_lock(:k)'), {'k': SWEEP_LOCK_KEY})\n"
            "        other.commit()\n"
            "        if held:\n"
            "            break\n"
            "        time.sleep(1)\n"
            "    busy = _sweep_with_new_session()\n"
            "    other.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': SWEEP_LOCK_KEY})\n"
            "    other.commit()\n"
            "results = []\n"
            "barrier = threading.Barrier(2)\n"
            "def run():\n"
            "    barrier.wait()\n"
            "    results.append(_sweep_with_new_session())\n"
            "threads = [threading.Thread(target=run) for _ in range(2)]\n"
            "[t.start() for t in threads]\n"
            "[t.join() for t in threads]\n"
            "for _ in range(30):\n"
            "    free = _sweep_with_new_session()\n"
            "    if free:\n"
            "        break\n"
            "    time.sleep(1)\n"
            "print(json.dumps({'held': held, 'busy': busy, 'parallel': sorted(results), 'free': free}))\n"
        )
    finally:
        cancelled = ctx.admin().call("POST", ctx.org_path(f"/deployments/{created['id']}/cancel"))
    expect(result["held"] is True, f"could not take the sweep lock: {result}")
    expect(result["busy"] == 0, f"a sweep ran while another replica held the lock: {result}")
    expect(result["free"] >= 1, f"the sweep did not tick the in-progress Deploy: {result}")
    # Two at once: one skips while the other holds the lock, or they ran back to back. Both must return.
    expect(len(result["parallel"]) == 2 and min(result["parallel"]) >= 0, f"parallel sweeps: {result}")
    expect(cancelled["status"] == "cancelled", f"cancel: {cancelled['status']}")
    return {"deployment_id": created["id"], **result}
