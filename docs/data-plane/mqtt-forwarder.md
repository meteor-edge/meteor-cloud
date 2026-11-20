# MQTT forwarder

`mqtt-forwarder` ([`src/data-plane/go/cmd/mqtt-forwarder`](../../src/data-plane/go/cmd/mqtt-forwarder/main.go)) is the **data-plane** service: the bridge between device MQTT and the control plane. It replaces the Python gateway in [`src/data-plane/data_plane`](../../src/data-plane/data_plane/) and speaks the same contract ([`contracts/mqtt-http.md`](../../contracts/mqtt-http.md)).

- **Device messages to the control plane.** Subscribes to `devices/+/status`, `devices/+/metrics`, `devices/+/commands/result` and `devices/+/events`, and sends each message to `POST {CONTROL_PLANE_URL}/internal/mqtt/ingest` with `{"topic", "payload"}` and `X-MQTT-Internal-Token`.
- **Control plane to devices.** Serves `POST /v1/publish`, `POST /v1/subscriptions` and `POST /v1/subscriptions/unwatch` on port 8081 (the address in the control plane's `DATA_PLANE_URL`).

It is one binary in the data API image (`meteorcloud/data-api`, `ghcr.io/meteor-edge/meteorcloud-data-api`), started with the entrypoint `mqtt-forwarder`.

## How a message travels

```text
device ──QoS 1──► broker ──$share/meteorcloud-mqtt-forwarder/devices/+/...──► forwarder
                                                                             │
                     shard = hash(device id) ─► bounded queue ─► worker ─────┤ POST /internal/mqtt/ingest
                                                                             │ (2xx) ─► MQTT PUBACK
```

1. **Shared subscription.** The forwarder subscribes in the group `MQTT_FORWARDER_SHARED_GROUP`. Run **one** forwarder (see [One forwarder](#one-forwarder)).
2. **Per-device order.** A message goes to the shard of its device (`devices/<id>/...`). One worker drains each shard in order, so a device's `status` changes (online, offline) are applied in the order they arrived, while different devices are forwarded in parallel. This holds inside one forwarder process.
3. **Small batches.** A worker takes up to `MQTT_FORWARDER_BATCH_SIZE` queued messages at once and sends them one after another over a kept-alive connection. (The ingest endpoint takes one message per request.)
4. **Acknowledged when done.** The MQTT acknowledgement goes to the broker only after the control plane answered 2xx, or the forwarder deliberately gave up on the message (see Failures). Until then the broker counts it as in flight.
5. **Backpressure, not loss.** `MQTT_FORWARDER_QUEUE_SIZE` is both the in-memory bound and the MQTT Receive Maximum. When the control plane is slow, the queue fills, the broker stops sending, and new messages wait in the broker. Device publishes are still acknowledged by the broker. Size the broker queue for the backlog you want to survive (Mosquitto `max_queued_messages`, see below).

### Failures

| Control plane answer | What happens |
| --- | --- |
| 2xx | Acknowledged. |
| Not reachable, timeout, 401, 403, 408, 429, 502, 503, 504 | Retried with backoff (100 ms doubling to 5 s) until it works. A wrong token or a restarting API loses nothing. |
| 500 and other 5xx | Retried up to `MQTT_FORWARDER_MAX_ATTEMPTS` times, then dropped (one bad message must not stop the device's shard for ever). |
| Other 4xx (400, 404, 422) | Dropped at once: it can never succeed. |

Dropped messages are logged (topic only, never the payload) and counted in `meteorcloud_dataplane_forward_dropped_total{reason}`.

Delivery is **at least once**. After a forwarder crash or a broker reconnect, messages that were not acknowledged come back from the broker and may reach the control plane twice. Status and metrics are last-value updates; command results are matched by command ID.

### Shutdown and restart

On SIGTERM the forwarder forwards what it already received for up to `MQTT_FORWARDER_DRAIN_TIMEOUT`, then disconnects **without leaving the shared group**. The broker keeps its session (`MQTT_FORWARDER_SESSION_EXPIRY`, 5 min): the subscriptions, the messages not yet acknowledged, and those devices publish while it restarts. The next forwarder with the same `MQTT_FORWARDER_CLIENT_ID` gets them all. Leaving the group would make the broker discard them. (The Python gateway used a clean session and lost what was published while it restarted.) Compose gives it `stop_grace_period: 30s`, Helm `terminationGracePeriodSeconds: 40`.

A forwarder that stays down longer than the session expiry loses the messages its session held.

### Retained device status

MQTT 5 sends no retained messages on a shared subscription, but the agent publishes its status (and its last will) retained, and the Python gateway received every device's retained status on connect. So after each connect the forwarder also subscribes plainly to `devices/+/status`, forwards the retained messages, and removes that subscription once none arrived for 2 s (at most 60 s). A live status published in that window reaches the control plane twice, one copy right after the other.

## One forwarder

Run one forwarder per installation, and do not overlap an old and a new one during an upgrade. Compose replaces the container (stop, then start), Helm uses one replica with `strategy: Recreate`, and the default client ID is fixed (`meteorcloud-mqtt-forwarder`), so a restart resumes the same broker session.

Why not several:

- **Order.** The broker spreads a shared group's messages over its members message by message. Two forwarders can each hold one of a device's messages; if one retries, the later message can reach the control plane first, and the control plane keeps the stale status. Order across forwarders would need device-affine routing or a control plane that rejects older status. Neither exists yet.
- **Orphaned sessions.** The broker keeps dispatching shared messages to a member's session while it is offline. A forwarder that goes away for good (a scale-down, or a new client ID) keeps collecting its share until the session expires, then those messages are lost.

The shared subscription still matters with one forwarder: it is what lets a future version spread the load once those two points are solved.

## Settings

The first block is the same as the Python service, so existing `.env` files, Compose, Helm and Ansible values keep working.

| Variable | Default | |
| --- | --- | --- |
| `CONTROL_PLANE_URL` | `http://127.0.0.1:8000` | Where `/internal/mqtt/ingest` lives |
| `HTTP_ADDR` | `:8081` | Publish/watch API, `/health`, `/metrics` |
| `MQTT_INTERNAL_TOKEN` | (required) | Checked on `/v1/*`, sent to the control plane |
| `MQTT_BROKER_HOST`, `MQTT_BROKER_PORT` | `localhost`, `8883` | TLS (`mqtts://`) like the Python service |
| `MQTT_PLATFORM_USERNAME`, `MQTT_PLATFORM_PASSWORD` | `platform`, (empty) | Broker login; `_FILE` variants read a file |
| `MQTT_CA_CERT_PATH` | `certs/ca.crt` | CA for the broker certificate |
| `MQTT_PROVIDER` | `mosquitto` | `mosquitto`, `emqx` or `vernemq` (checked, otherwise unused) |
| `MQTT_ENABLED` | `true` | `false`: no broker connection, `/v1/*` answer 503 |
| `METRICS_ENABLED` | `true` | `false` hides `/metrics` |
| `LOG_FORMAT`, `LOG_LEVEL` | `console`, `info` | `json` for log shipping |

New settings:

| Variable | Default | |
| --- | --- | --- |
| `MQTT_FORWARDER_BROKER_URL` | `mqtts://HOST:PORT` | Overrides host and port (`mqtt://` for a plain listener) |
| `MQTT_FORWARDER_CLIENT_ID` | `meteorcloud-mqtt-forwarder` | Keep it the same across restarts, so the forwarder resumes its broker session |
| `MQTT_FORWARDER_SHARED_GROUP` | `meteorcloud-mqtt-forwarder` | Set to an empty value for a plain subscription |
| `MQTT_FORWARDER_WORKERS` | `16` | Shards, and parallel requests to the control plane |
| `MQTT_FORWARDER_QUEUE_SIZE` | `4096` | Messages held in memory; also the MQTT Receive Maximum (max 65535) |
| `MQTT_FORWARDER_BATCH_SIZE` | `32` | Messages a worker takes at once |
| `MQTT_FORWARDER_MAX_ATTEMPTS` | `10` | Tries for a 500-type answer before dropping |
| `MQTT_FORWARDER_INGEST_TIMEOUT` | `10s` | One ingest request (same as the Python service) |
| `MQTT_FORWARDER_SESSION_EXPIRY` | `5m` | Broker keeps the session (and unacknowledged messages) this long |
| `MQTT_FORWARDER_DRAIN_TIMEOUT` | `20s` | Shutdown wait for queued messages |

Helm: `dataPlane.forwarder` (keys in camel case, e.g. `queueSize: 8192`).

### Broker queue

A backlog bigger than the forwarder queue waits in the broker. The bundled Mosquitto keeps up to `max_queued_messages 100000` per client (raised from 1000, which dropped messages when the control plane fell about 2,000 messages behind). EMQX (`mqtt.max_mqueue_len`) and VerneMQ (`max_online_messages`) default to 1000; raise them the same way if you run those brokers with bursty fleets.

## Metrics

`GET :8081/metrics`. Names and labels of the Python service are kept, so the bundled dashboards and alerts work unchanged.

| Metric | Labels | |
| --- | --- | --- |
| `meteorcloud_dataplane_mqtt_connected` | | 1 while connected and subscribed |
| `meteorcloud_dataplane_mqtt_messages_received_total` | kind | `status`, `metrics`, `command_result`, `other` |
| `meteorcloud_dataplane_ingest_forward_total` | result | `ok`, `http_error`, `unreachable`, one per attempt |
| `meteorcloud_dataplane_publish_total` | result | `/v1/publish` outcomes |
| `http_requests_total`, `http_request_duration_seconds` | method, path, status | API requests (not `/health`, `/metrics`) |
| `meteorcloud_dataplane_forward_queue_depth`, `..._queue_capacity` | | Waiting messages and the bound |
| `meteorcloud_dataplane_forward_dropped_total` | reason | `queue_full` (QoS 0 only), `rejected`, `retries_exhausted` |
| `meteorcloud_dataplane_forward_retries_total` | | Repeated ingest attempts |
| `meteorcloud_dataplane_forward_batch_size` | | Messages per worker take |
| `meteorcloud_dataplane_forward_delay_seconds` | | MQTT receive to control-plane acceptance |

Also the standard `go_*` and `process_*` metrics. The Python service had `python_*` instead.

A queue that stays near capacity with `forward_retries_total` rising means the control plane is slow or down. Rising `dropped_total{reason="rejected"}` means devices send something the control plane refuses.

## Watched topics

`POST /v1/subscriptions` (the console's live MQTT view) subscribes the forwarder to the topic filter, outside the shared group, and forwards those messages too. Calls are reference-counted per topic, as before, and subscription changes are serialized so the broker always ends up with exactly the watched topics. Watches are process state: they are removed on shutdown, and the control plane opens them again with its next live view.

## Fallback to the Python gateway (one release)

| | Go forwarder (default) | Python gateway |
| --- | --- | --- |
| Compose | profile `mqtt` (service `data-plane`) | profile `mqtt-python` instead of `mqtt` (service `data-plane-python`, also reachable as `data-plane`) |
| Ansible | `data_plane_gateway: go` | `data_plane_gateway: python` |
| Helm | `dataPlane.gateway: go` | `dataPlane.gateway: python` |
| `make test-mqtt` | `DATA_PLANE_SERVICE=data-plane` | `DATA_PLANE_SERVICE=data-plane-python` |

Never run both: each forwards every device message. The Python gateway and its image (`meteorcloud-data-plane`) are removed in the next release.

## Tests

```bash
cd src/data-plane/go
go test -race ./internal/mqttfwd/                         # unit tests, no broker

# with a real broker: shared subscription, watch, and a 1,000 msg/s QoS 1 burst
docker run -d --rm --name mqttfwd-broker -p 127.0.0.1:1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
MQTTFWD_TEST_BROKER_URL=mqtt://127.0.0.1:1883 go test -race -run Broker -v ./internal/mqttfwd/
```

`make test-mqtt` runs the live MQTT suite against the full Compose stack, including `tests/mqtt_live/test_mqtt_burst.py`. That test sends 1,000 msg/s for 5 s from one device and checks that the control plane counted every message (`meteorcloud_mqtt_messages_ingested_total`) and the forwarder dropped none. `MQTT_BURST_RATE`, `MQTT_BURST_SECONDS` and `MQTT_BURST=0` change or skip it.
