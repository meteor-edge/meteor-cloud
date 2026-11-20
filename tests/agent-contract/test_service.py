"""The background service: heartbeats, MQTT, actions, Wi-Fi, WireGuard and a docker deploy."""

from __future__ import annotations

import hashlib
import uuid

from harness.broker import Observer
from harness.device import APP, LOADED_IMAGE
from harness.ed25519 import b64url, public_key
from harness.fake_cp import Reply
from harness.script import accepted, heartbeat, now, register_and_basics

ARTIFACT = b"docker save tarball of demo/app:1.0 (simulated)\n"


def test_heartbeat_mqtt(contract, agent, device, broker) -> None:
    """Two heartbeats with MQTT on: credentials refresh, status, metrics, events, ping."""
    connect = contract.broker(broker)
    device_id = register_and_basics(contract, mqtt=connect)
    contract.script("GET", "/api/v1/agent/mqtt", Reply(200, {"enabled": True, "config_version": "mqtt-v2"}))
    contract.script("POST", "/api/v1/agent/mqtt/credentials",
                    Reply(200, {**connect, "password": contract.secret("rotated_mqtt_password"), "config_version": "mqtt-v2"}))
    device.journal.append({"message": "Under-voltage detected", "priority": 4, "unit": "kernel",
                           "source": "journal", "cursor": "c1", "timestamp": now()})
    assert agent.register(contract.plane.url, contract.secret("registration_token")) == 0
    observer = Observer(broker, agent.device_id or device_id)
    ping_id = str(uuid.uuid4())

    def between(beat: int) -> None:
        if beat == 1:
            observer.wait_for(lambda m: m.topic.endswith("/status") and m.payload.get("status") == "online")
            observer.wait_for(lambda m: m.topic.endswith("/metrics"))
            observer.publish("commands", {"type": "sync_desired", "revision": 3})
            observer.publish("commands", {"type": "ping", "command_id": ping_id})
            observer.wait_for(lambda m: m.topic.endswith("/commands/result") and m.payload.get("command_id") == ping_id)
        if beat == 2:
            observer.wait_for(lambda m: m.topic.endswith("/events") and m.payload.get("type") == "app.started")
            observer.wait_for(lambda m: m.topic.endswith("/events") and m.payload.get("type") == "log")

    assert agent.run(heartbeats=2, between=between) == 0
    observer.wait_for(lambda m: m.topic.endswith("/status") and m.payload.get("status") == "offline")
    retained = observer.retained("status")
    observer.stop()
    assert retained == {"status": "offline"}
    contract.finish(observer, retained={"status": retained})


def test_actions(contract, agent, device) -> None:
    """Restart is reported before it runs; an unknown action kind is reported failed: unsupported."""
    device_id = register_and_basics(contract)
    contract.script("POST", "/api/v1/agent/heartbeat",
                    heartbeat(device_id, actions_pending=True), heartbeat(device_id))
    contract.script("GET", "/api/v1/agent/actions", Reply(200, {"actions": [
        {"id": str(uuid.uuid4()), "kind": "reboot"},
        {"id": str(uuid.uuid4()), "kind": "set_password", "username": "pi", "password": contract.secret("user_password")},
        {"id": str(uuid.uuid4()), "kind": "add_user", "username": "ops", "password": contract.secret("new_user_password"),
         "sudo": True, "authorized_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOpsKeyOpsKeyOpsKeyOpsKeyOpsKeyOpsKey ops"},
        {"id": str(uuid.uuid4()), "kind": "frobnicate"},
    ]}, off_contract="future action kind: agents must report it failed, unsupported"), Reply(200, {"actions": []}))
    contract.script("POST", "/api/v1/agent/actions/report", accepted(1), accepted(3))

    assert agent.register(contract.plane.url, contract.secret("registration_token")) == 0
    assert agent.run(heartbeats=2) == 0

    assert [a[0] for a in device.host_actions] == ["reboot", "set_password", "add_user"]
    reports = contract.plane.calls("POST", "/api/v1/agent/actions/report")
    assert [r["state"] for r in reports[0].request.body["results"]] == ["done"]
    unknown = reports[1].request.body["results"][-1]
    assert unknown["state"] == "failed" and unknown["error"].lower().startswith("unsupported")
    contract.finish()


def test_wifi(contract, agent, device) -> None:
    """Join a new network (checked against the server before it is kept) and forget another."""
    device.wifi_saved.append("old-lab")
    device_id = register_and_basics(contract)
    contract.script("POST", "/api/v1/agent/heartbeat", heartbeat(device_id, wifi_pending=True), heartbeat(device_id))
    contract.script("GET", "/api/v1/agent/network/wifi", Reply(200, {"changes": [
        {"id": str(uuid.uuid4()), "revision": 1, "action": "apply", "ssid": "warehouse", "security": "wpa-psk",
         "hidden": False, "connect_now": True, "password": contract.secret("wifi_password")},
        {"id": str(uuid.uuid4()), "revision": 2, "action": "remove", "ssid": "old-lab", "security": "wpa-psk",
         "hidden": False, "connect_now": False, "password": None},
    ]}))
    contract.script("POST", "/api/v1/agent/network/wifi/report", accepted(2))

    assert agent.register(contract.plane.url, contract.secret("registration_token")) == 0
    assert agent.run(heartbeats=2) == 0

    assert device.wifi_ssid == "warehouse" and "old-lab" not in device.wifi_saved
    assert [c for c, _ in device.wifi_requests if c in ("apply", "confirm", "remove")] == ["apply", "confirm", "remove"]
    report = contract.plane.calls("POST", "/api/v1/agent/network/wifi/report")[0].request.body
    assert [r["state"] for r in report["results"]] == ["applied", "removed"]
    assert "password" not in str(report)
    contract.finish()


def test_wireguard(contract, agent, device) -> None:
    """Register the public key, bring the tunnel up, trust the SSH CA, and say so at once."""
    device_id = register_and_basics(contract)
    reply = Reply(200, {
        "status": "active", "provider": "wireguard", "wg_address": "10.8.0.12/32",
        "server_public_key": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "server_endpoint": "127.0.0.1:51820", "allowed_ips": "10.8.0.1/32", "persistent_keepalive": 25,
        "ssh_user": "pi", "ssh_gateway_public_key": None, "ssh_gateway_key_retired": False,
        "ssh_ca_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIContractSshCaContractSshCaContractSsh",
        "ssh_principal": f"device-{device_id}",
    })
    contract.script("POST", "/api/v1/agent/connectivity/wireguard", reply)

    assert agent.register(contract.plane.url, contract.secret("registration_token")) == 0
    assert agent.run(heartbeats=1) == 0

    first, second = contract.plane.calls("POST", "/api/v1/agent/connectivity/wireguard")
    assert first.request.body.get("ssh_ca_public_key") is None
    assert second.request.body["ssh_ca_public_key"].startswith("ssh-ed25519 ")
    assert ["wg-quick", "up"] == device.wg_commands[-1][:2]
    contract.finish()


def test_docker_deploy(contract, agent, device) -> None:
    """A signed docker release: pin the key, download through a storage redirect, load, run, report."""
    register_and_basics(contract)
    artifact_id = str(uuid.uuid4())
    digest = hashlib.sha256(ARTIFACT).hexdigest()
    item = {
        "workload_key": APP, "kind": "docker", "release_id": str(uuid.uuid4()), "release_name": "demo",
        "release_version": "1.0.0", "artifact_id": artifact_id, "revision": 1, "artifact_sha256": digest,
        "spec": {"container_name": APP, "ports": ["8080:80"], "restart": "unless-stopped"},
    }
    seed_public = b64url(public_key(bytes(range(32))))
    contract.script("GET", "/api/v1/agent/software", lambda _req: Reply(200, [contract.signed(item, agent.device_id)]))
    contract.script("GET", "/api/v1/agent/signing-keys", Reply(200, [{
        "key_id": "org-key-1", "algorithm": "ed25519", "public_key": seed_public, "endorsed_by": None,
        "endorsement": None, "created_at": now(), "retired_at": None,
    }]))
    contract.script("GET", f"/api/v1/agent/artifacts/{artifact_id}/download", Reply(
        302, None, {"Location": f"{contract.plane.url}/storage/{artifact_id}?X-Amz-Signature=abc",
                    "X-Checksum-SHA256": digest, "Cache-Control": "no-store"}))
    contract.script("GET", f"/storage/{artifact_id}", Reply(200, ARTIFACT))

    assert agent.register(contract.plane.url, contract.secret("registration_token")) == 0
    assert agent.run(heartbeats=2) == 0

    assert device.containers == {APP: LOADED_IMAGE}
    reports = [r for e in contract.plane.calls("POST", "/api/v1/agent/heartbeat")
               for r in (e.request.body or {}).get("software_report", [])]
    assert reports[0]["sync_status"] == "updating"
    assert reports[-1]["sync_status"] == "in_sync" and reports[-1]["installed_version"] == "1.0.0"
    contract.finish()
