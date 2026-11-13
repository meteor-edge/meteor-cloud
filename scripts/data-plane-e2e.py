#!/usr/bin/env python3
"""End-to-end check of the application data plane: the whole developer workflow.

Against a running stack (control plane, data API, ClickHouse or PostgreSQL, EMQX) it:

  1. registers a device and two applications with scoped permissions
  2. deploys a Docker release bound to an application
  3. runs the real device agent, which obtains a token, mounts it and starts the container
  4. sees the containerised application publish through the SDK
  5. subscribes and queries from a second, cloud-hosted application
  6. sends commands (application to application, and operator through the control plane)
  7. rotates the token under the running container, then revokes access
  8. checks tenant isolation, direct MQTT with a short-lived token, and the audit trail

Usage (stack from `make dev`, admin from the seed):

    python scripts/data-plane-e2e.py

Environment:
    E2E_CONTROL_PLANE_URL   default http://localhost:8000
    E2E_DATA_API_URL        default http://localhost:8082 (as reachable from this machine)
    E2E_EMAIL / E2E_PASSWORD  an organization owner (default: the seed owner)
    E2E_EDGE_IMAGE          image of sdk/python/examples/edge-app (built when unset)
    E2E_MQTT_CA             CA certificate of the broker, to test direct MQTT (skipped when unset)

Needs Docker on this machine and: pip install -e sdk/python -e src/device-plane/agent
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "sdk/python/src"), str(REPO / "src/device-plane/agent"), str(REPO / "src/device-plane")]

from edge_agent.client import EdgeClient  # noqa: E402
from edge_agent.software import reconcile_software  # noqa: E402

import meteorcloud  # noqa: E402
from meteorcloud import MeteorClient  # noqa: E402

CP = os.environ.get("E2E_CONTROL_PLANE_URL", "http://localhost:8000").rstrip("/")
DATA_API = os.environ.get("E2E_DATA_API_URL", "http://localhost:8082").rstrip("/")
EMAIL = os.environ.get("E2E_EMAIL", "owner@example.com")
PASSWORD = os.environ.get("E2E_PASSWORD", "dev-password-123")
RUN = uuid.uuid4().hex[:6]
CONTAINER = f"meter-e2e-{RUN}"
passed: list[str] = []


def step(name: str) -> None:
    passed.append(name)
    print(f"  ok  {name}", flush=True)


def wait_for(what: str, predicate, timeout: float = 30.0, pause: float = 0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(pause)
    raise AssertionError(f"timed out waiting for {what}")


def expect(error: type[Exception], fn, *args, **kwargs) -> Exception:
    try:
        fn(*args, **kwargs)
    except error as caught:
        return caught
    raise AssertionError(f"expected {error.__name__} from {getattr(fn, '__qualname__', fn)}")


class Api:
    """Console/API user of the control plane."""

    def __init__(self, email: str, password: str) -> None:
        self.http = httpx.Client(base_url=CP, timeout=20)
        login = self.http.post("/api/v1/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        self.org = (self.http.get("/api/v1/organizations").json() or [{"id": None}])[0]["id"]

    @classmethod
    def new_tenant(cls) -> Api:
        """A new user with an organization of their own."""
        email, password = f"e2e-{RUN}@example.com", f"e2e-password-{uuid.uuid4().hex}"
        created = httpx.post(f"{CP}/api/v1/auth/register", timeout=20, json={
            "email": email, "full_name": "E2E other tenant", "password": password})
        assert created.status_code == 201, created.text
        tenant = cls(email, password)
        organization = tenant.http.post("/api/v1/organizations", json={"name": f"E2E other {RUN}"})
        assert organization.status_code == 201, organization.text
        tenant.org = organization.json()["id"]
        return tenant

    def call(self, method: str, path: str, expected: int | tuple[int, ...] = (200, 201, 202, 204), **kwargs):
        response = self.http.request(method, f"/api/v1/organizations/{self.org}{path}", **kwargs)
        expected = (expected,) if isinstance(expected, int) else expected
        assert response.status_code in expected, f"{method} {path}: {response.status_code} {response.text}"
        return response.json() if response.content else None


def edge_image() -> str:
    image = os.environ.get("E2E_EDGE_IMAGE")
    if image:
        return image
    image = "meteorcloud/example-edge-app:e2e"
    subprocess.run(["docker", "build", "-q", "-t", image, str(REPO / "sdk/python/examples/edge-app")], check=True)
    return image


def container_running() -> bool:
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], capture_output=True, text=True)
    return out.stdout.strip() == "true"


def main() -> int:
    api = Api(EMAIL, PASSWORD)
    work = Path(tempfile.mkdtemp(prefix="meteor-e2e-"))
    meter_slug, dash_slug = f"meter-{RUN}", f"dashboard-{RUN}"
    print(f"Data plane end-to-end run {RUN} (organization {api.org})")

    # 1. Device and applications ------------------------------------------------
    registration = api.call("POST", "/registration-tokens", json={"name": f"e2e-{RUN}"})["token"]
    device = httpx.post(f"{CP}/api/v1/agent/register", timeout=20, json={
        "token": registration, "mac_addresses": [f"02:e2:e0:{RUN[0:2]}:{RUN[2:4]}:{RUN[4:6]}"], "name": f"e2e-{RUN}"})
    device.raise_for_status()
    device_id, device_token = device.json()["device_id"], device.json()["device_token"]
    step("device registered")

    meter = api.call("POST", "/applications", json={
        "slug": meter_slug, "name": "Meter", "permissions": {
            "publish": ["energy.**", "alerts.*"], "receive_commands": True}})
    dashboard = api.call("POST", "/applications", json={
        "slug": dash_slug, "name": "Dashboard", "permissions": {
            "subscribe": ["energy.**", "alerts.*", "meteor.lifecycle"], "read": ["energy.**", "alerts.*"],
            "send_commands": [meter_slug]}})
    dash_credential = api.call("POST", f"/applications/{dashboard['id']}/credentials", json={
        "name": "e2e cloud dashboard", "environment": "staging", "expires_in_days": 1})
    assert dash_credential["token"].startswith("mca_")
    step("applications registered with scoped permissions; cloud credential issued")

    # 2. Release bound to the application, deployed to the device ------------------
    release = api.call("POST", "/releases", json={
        "name": CONTAINER, "version": "1.0.0", "kind": "docker", "spec": {
            "container_name": CONTAINER, "image": edge_image(), "env": {"PUBLISH_INTERVAL_SECONDS": "1"},
            "meteorcloud": {"application": meter_slug, "environment": "production"}}})
    api.call("POST", "/deployments", json={"release_id": release["id"], "target": {"device_ids": [device_id]}})
    step("release bound to the application and deployed")

    # 3. The real agent applies it -------------------------------------------------
    agent = EdgeClient(CP)
    artifacts = work / "artifacts"
    reports = reconcile_software(agent, device_token, artifacts)
    report = next(r for r in reports if r["workload_key"] == CONTAINER)
    assert report["sync_status"] == "in_sync", report
    token_file = next((work / "app-credentials").glob(f"{CONTAINER}-*/token"))
    first_token = token_file.read_text().strip()
    inspect = json.loads(subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, text=True,
                                        check=True).stdout)[0]
    env = dict(item.split("=", 1) for item in inspect["Config"]["Env"])
    assert env["METEORCLOUD_TOKEN_FILE"] == "/run/secrets/meteorcloud-token" and env["METEORCLOUD_ENDPOINT"]
    assert env["METEORCLOUD_APP_ID"] == meter_slug and env["METEORCLOUD_DEVICE_ID"] == device_id
    assert first_token not in json.dumps(inspect), "the token leaked into the container configuration"
    assert any(m["Destination"] == "/run/secrets/meteorcloud-token" and not m["RW"] for m in inspect["Mounts"])
    step("agent obtained a token, mounted it read-only and started the container (no token in env or image)")

    try:
        dash = MeteorClient(endpoint=DATA_API, token=dash_credential["token"], retry_time=20)
        me = dash.whoami()
        assert (me.application, me.environment, me.device_id) == (dash_slug, "staging", None)

        # 4 + 5. Published by the container, seen live and in history from the cloud ----
        live = dash.telemetry.subscribe("energy.*")
        event = live.get(timeout=40)
        assert (event.application, event.device_id, event.environment) == (meter_slug, device_id, "production")
        assert event.organization_id == api.org and "power" in event.data
        step("container published with the SDK; cloud application received it live")

        history = wait_for("history", lambda: dash.telemetry.query(
            "energy.metrics", limit=3, device_id=device_id).events)
        assert all(e.device_id == device_id for e in history)
        buckets = dash.telemetry.aggregate("energy.metrics", "power", function="avg", bucket="1m",
                                           device_id=device_id)
        assert sum(b.count for b in buckets) >= 1 and all(900 <= b.value <= 1500 for b in buckets if b.count)
        assert len(dash.telemetry.query("energy.metrics", device_id=str(uuid.uuid4()))) == 0
        step("history query, device filter and bucketed aggregate")

        # Least privilege.
        assert expect(meteorcloud.PublishError, dash.telemetry.publish, "energy.metrics", {"x": 1}).code == (
            "forbidden_stream")
        expect(meteorcloud.PermissionDeniedError, dash.telemetry.query, "billing.invoices")
        expect(meteorcloud.PermissionDeniedError, dash.telemetry.subscribe, "billing.*")
        step("least privilege: publish, read and subscribe outside the grants are refused")

        # 6. Commands --------------------------------------------------------------
        answer = dash.commands.call(meter_slug, "set-interval", {"seconds": 0.5}, device_id=device_id, timeout=30)
        assert answer == {"interval": 0.5}, answer
        failed = expect(meteorcloud.CommandError, dash.commands.call, meter_slug, "no-such-command",
                        device_id=device_id, timeout=30)
        assert failed.command.state == "FAILED"
        step("application to application command: delivered, executed, acknowledged; unknown command fails")

        sent = api.call("POST", f"/applications/{meter['id']}/commands", json={
            "name": "set-interval", "payload": {"seconds": 1}, "device_id": device_id})
        final = wait_for("operator command", lambda: (
            lambda c: c if c["state"] in ("SUCCEEDED", "FAILED", "TIMEOUT") else None)(
                api.call("GET", f"/applications/{meter['id']}/commands/{sent['id']}")))
        assert final["state"] == "SUCCEEDED" and final["result"] == {"interval": 1.0}, final
        assert final["issued_by"].startswith("user:"), final
        step("operator command through the control plane reached the container")

        # 7. Rotation, then revocation ----------------------------------------------
        meta_path = token_file.parent / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["refresh_after"] = "2000-01-01T00:00:00+00:00"  # make the agent consider it due
        meta_path.write_text(json.dumps(meta))
        started = inspect["State"]["StartedAt"]
        reports = reconcile_software(agent, device_token, artifacts)
        assert next(r for r in reports if r["workload_key"] == CONTAINER)["sync_status"] == "in_sync"
        second_token = token_file.read_text().strip()
        assert second_token != first_token
        now_started = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", CONTAINER],
                                     capture_output=True, text=True).stdout.strip()
        assert now_started == started, "the container was restarted by a rotation"
        credentials = api.call("GET", f"/applications/{meter['id']}/credentials")
        old = next(c for c in credentials if c["token_prefix"] and first_token.startswith(c["token_prefix"]))
        api.call("POST", f"/applications/{meter['id']}/credentials/{old['id']}/revoke")
        time.sleep(8)  # more than one policy poll: the old token is gone everywhere
        with MeteorClient(endpoint=DATA_API, token=first_token, retry_time=3) as stale:
            stale._async._http._token_loaded_at = 0
            expect(meteorcloud.AuthenticationError, stale.whoami)
        while True:  # drain what was buffered, then prove fresh data still arrives
            try:
                live.get(timeout=0.2)
            except TimeoutError:
                break
        fresh = live.get(timeout=30)
        assert fresh.device_id == device_id and container_running()
        step("token rotated under the running container without a restart; the old token stopped working")

        # 8a. Tenant isolation --------------------------------------------------------
        if True:
            other = Api.new_tenant()
            assert other.org != api.org
            permissions = {"subscribe": ["energy.**"], "read": ["energy.**"], "publish": ["energy.**"]}
            # Granting commands to an application of another organization is refused outright.
            other.call("POST", "/applications", expected=422, json={
                "slug": f"spy-{RUN}", "name": "Spy", "permissions": {**permissions, "send_commands": [meter_slug]}})
            spy = other.call("POST", "/applications", json={
                "slug": f"spy-{RUN}", "name": "Spy", "permissions": permissions})
            spy_token = other.call("POST", f"/applications/{spy['id']}/credentials", json={"name": "spy"})["token"]
            time.sleep(6)
            with MeteorClient(endpoint=DATA_API, token=spy_token, retry_time=10) as spy_client:
                assert len(spy_client.telemetry.query("energy.metrics")) == 0
                with spy_client.telemetry.subscribe("energy.**") as foreign:
                    try:
                        foreign.get(timeout=3)
                        raise AssertionError("another organization received this organization's data")
                    except TimeoutError:
                        pass
                expect(meteorcloud.PermissionDeniedError, spy_client.commands.send, meter_slug, "set-interval",
                       device_id=device_id)
            other.call("GET", f"/applications/{meter['id']}", expected=(403, 404))
            step("tenant isolation: another organization sees no data and cannot command the application")

        # 8b. Direct MQTT with a short-lived token --------------------------------------
        ca = os.environ.get("E2E_MQTT_CA")
        if ca:
            mqtt_direct(dash_credential["token"], ca, device_id)

        # 8c. Audit trail ------------------------------------------------------------
        actions = {e["action"] for e in api.call("GET", "/audit-events", params={"limit": 500})}
        wanted = {"application.create", "application.credential.create", "application.credential.mint",
                  "application.credential.revoke", "application.command.send"}
        assert wanted <= actions, f"missing audit events: {sorted(wanted - actions)}"
        step("audit trail has application, credential and command events")

        # Disable the application: everything it holds stops working.
        api.call("PATCH", f"/applications/{meter['id']}", json={"enabled": False})
        time.sleep(8)
        with MeteorClient(endpoint=DATA_API, token=second_token, retry_time=3) as disabled:
            disabled._async._http._token_loaded_at = 0
            expect(meteorcloud.AuthenticationError, disabled.whoami)
        step("disabling the application cut off its credentials")
        dash.close()
    except BaseException:
        logs = subprocess.run(["docker", "logs", "--tail", "40", CONTAINER], capture_output=True, text=True)
        print(f"--- last log lines of {CONTAINER} ---\n{logs.stdout}{logs.stderr}", file=sys.stderr)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)

    print(f"\nPASSED: {len(passed)} checks")
    return 0


def mqtt_direct(token: str, ca: str, device_id: str) -> None:
    """Standard MQTT client, no SDK: exchange the token for a broker login with an embedded ACL."""
    import paho.mqtt.client as mqtt

    grant = httpx.post(f"{DATA_API}/v1/mqtt/credentials", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    grant.raise_for_status()
    login = grant.json()
    host, port = login["url"].split("://", 1)[1].rsplit(":", 1)
    host = os.environ.get("E2E_MQTT_HOST", host)
    received: list[dict] = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=login["client_id"], protocol=mqtt.MQTTv5)
    client.username_pw_set(login["username"], login["password"])
    context = ssl.create_default_context(cafile=ca)
    context.check_hostname = os.environ.get("E2E_MQTT_VERIFY_HOSTNAME", "true") == "true"
    client.tls_set_context(context)
    client.on_message = lambda _c, _u, message: received.append(json.loads(message.payload))
    client.on_connect = lambda c, *_: c.subscribe(f"{login['live_root']}/energy/metrics", qos=1)
    client.connect(host, int(port))
    client.loop_start()
    try:
        event = wait_for("an event over MQTT", lambda: received[0] if received else None, 30)
        assert event["device_id"] == device_id and event["stream"] == "energy.metrics"
    finally:
        client.loop_stop()
        client.disconnect()
    bad = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=login["client_id"] + "-x", protocol=mqtt.MQTTv5)
    bad.username_pw_set(login["username"], "not-a-token")
    bad.tls_set_context(context)
    codes: list[str] = []
    bad.on_connect = lambda _c, _u, _f, reason, _p: codes.append(str(reason))
    bad.connect(host, int(port))
    bad.loop_start()
    wait_for("the broker's refusal", lambda: codes, 15)
    bad.loop_stop()
    assert codes[0] != "Success", codes
    step("direct MQTT: short-lived login works, live topic delivers, a wrong password is refused")


if __name__ == "__main__":
    sys.exit(main())
