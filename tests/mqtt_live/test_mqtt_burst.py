"""A device burst (default 1,000 msg/s for 5 s, QoS 1) reaches the control plane in full.

Counts come from Prometheus metrics: the control plane's
``meteorcloud_mqtt_messages_ingested_total{kind="metrics",result="applied"}``
and, when reachable, the forwarder's own counters. These count all devices, so
the test assumes nothing else publishes metrics during the burst (true for the
live suite, which runs one test at a time); the burst device's own last
metrics are checked too. Runs where those endpoints are reachable (local Compose); elsewhere it
is skipped. MQTT_BURST=0 skips it, MQTT_BURST_RATE / MQTT_BURST_SECONDS size it.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.request
import uuid

import paho.mqtt.client as mqtt
import pytest

from mqtt_live.http import wait_until

RATE = int(os.environ.get("MQTT_BURST_RATE", "1000"))
SECONDS = int(os.environ.get("MQTT_BURST_SECONDS", "5"))
CP_METRICS = os.environ.get(
    "CONTROL_PLANE_METRICS_URL", "http://127.0.0.1:8000/metrics"
)
FWD_METRICS = os.environ.get("DATA_PLANE_METRICS_URL", "http://127.0.0.1:8081/metrics")


def _scrape(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read().decode()
    except OSError:  # URLError, connection refused, timeout
        return None


def _sample(text: str, name: str, **labels: str) -> float:
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(name + "{") and not line.startswith(name + " "):
            continue
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            total += float(line.rsplit(" ", 1)[1])
    return total


def _counts() -> dict[str, float]:
    cp = _scrape(CP_METRICS) or ""
    fwd = _scrape(FWD_METRICS) or ""
    return {
        "ingested": _sample(
            cp,
            "meteorcloud_mqtt_messages_ingested_total",
            kind="metrics",
            result="applied",
        ),
        "forward_ok": _sample(
            fwd, "meteorcloud_dataplane_ingest_forward_total", result="ok"
        ),
        "received": _sample(
            fwd, "meteorcloud_dataplane_mqtt_messages_received_total", kind="metrics"
        ),
        "dropped": _sample(fwd, "meteorcloud_dataplane_forward_dropped_total"),
    }


def test_burst_reaches_control_plane(api, live_config):
    if os.environ.get("MQTT_BURST", "1") == "0":
        pytest.skip("MQTT_BURST=0")
    cp_text = _scrape(CP_METRICS)
    if (
        not cp_text
        or "meteorcloud_mqtt_messages_ingested_total" not in cp_text
        and "# HELP" not in cp_text
    ):
        pytest.skip(f"control-plane metrics not reachable at {CP_METRICS}")
    forwarder = bool(
        re.search(
            r"meteorcloud_dataplane_forward_queue_capacity", _scrape(FWD_METRICS) or ""
        )
    )

    device = api.register_device("burst")
    topic = f"devices/{device.device_id}/metrics"
    total = RATE * SECONDS

    acked = threading.Semaphore(0)
    connected = threading.Event()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"device-{device.device_id}-burst-{uuid.uuid4().hex[:6]}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(device.mqtt_username, device.mqtt_password)
    client.tls_set(ca_certs=live_config.mqtt_ca_file, cert_reqs=ssl.CERT_REQUIRED)
    client.max_inflight_messages_set(1000)
    client.max_queued_messages_set(0)
    client.on_connect = lambda *_a, **_k: connected.set()
    client.on_publish = lambda *_a, **_k: acked.release()
    client.connect(live_config.mqtt_host, live_config.mqtt_port, 30)
    client.loop_start()
    try:
        assert connected.wait(15), "device did not connect"
        before = _counts()
        start = time.monotonic()
        for n in range(total):
            # Pace to RATE: sleep until message n is due.
            due = start + n / RATE
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            payload = json.dumps(
                {"timestamp": "2026-11-20T12:00:00Z", "cpu_percent": n % 97, "seq": n}
            )
            client.publish(topic, payload, qos=1)
        for _ in range(total):
            assert acked.acquire(timeout=60), "broker did not acknowledge every publish"
        sent_in = time.monotonic() - start
        assert wait_until(
            lambda: _counts()["ingested"] - before["ingested"] >= total,
            timeout_seconds=120,
        ), (
            f"control plane ingested {_counts()['ingested'] - before['ingested']:.0f} of {total} "
            f"(before {before}, now {_counts()})"
        )
        # The burst device's own record ends with its last message.
        last_cpu = (total - 1) % 97
        assert wait_until(
            lambda: (
                (api.get_device(device.device_id).get("mqtt_metrics") or {}).get(
                    "cpu_percent"
                )
                == last_cpu
            ),
            timeout_seconds=30,
        ), "the burst device's last metrics did not reach the control plane"
        if forwarder:
            # The control plane counter can lead the forwarder's by the time the
            # forwarder records the 2xx; wait for both.
            assert wait_until(
                lambda: (
                    _counts()["forward_ok"] - before["forward_ok"] >= total
                    and _counts()["received"] - before["received"] >= total
                ),
                timeout_seconds=30,
            ), f"forwarder counters lag: before {before}, now {_counts()}"
        after = _counts()
        print(
            f"burst: {total} messages in {sent_in:.1f}s ({total / sent_in:.0f} msg/s); "
            f"all ingested after {time.monotonic() - start:.1f}s; deltas "
            + ", ".join(f"{k}={after[k] - before[k]:.0f}" for k in after)
        )
        if forwarder:
            assert after["received"] - before["received"] >= total
            assert after["forward_ok"] - before["forward_ok"] >= total
            assert after["dropped"] - before["dropped"] == 0
    finally:
        client.loop_stop()
        client.disconnect()
