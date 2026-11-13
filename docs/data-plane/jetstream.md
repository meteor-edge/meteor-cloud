# Device event path: NATS JetStream

Device agents publish metrics, status and events over MQTT. The device event path turns them into versioned events on **NATS JetStream**, where independent consumers process them: the **rule processor** (real-time rules and alerts) and the **ClickHouse writer** (history).

JetStream is internal messaging between data-plane services. **Devices never connect to NATS**, and the control plane does not depend on it.

```
 device agent ── MQTT over TLS ──▶ broker (Mosquitto / EMQX / VerneMQ)
                                        │ $share/meteor-device-ingest/devices/+/{metrics,status,events}
                                        ▼
                                 device-ingest (Go)   ◀── device directory (pull) ── control plane
                                   validate topic, bind device → organization,
                                   validate + normalize, publish, then ack MQTT
                                        │
                                        ▼
                    NATS JetStream stream METEOR_DEVICE (file storage, R1 dev / R3 prod)
                      telemetry.v1.<org>.<device>
                      device.status.v1.<org>.<device>
                      device.events.v1.<org>.<device>
                         │                                   │
           durable "rule-processor"               durable "clickhouse-writer"
                         ▼                                   ▼
                 rule-processor (Go)                 clickhouse-writer (Go)
                 Handler (rule engine)               batched, idempotent inserts
                         ▼                                   ▼
                  alerts / actions                  ClickHouse `events` table
```

The existing path from the Python data plane to the control plane (`POST /internal/mqtt/ingest`, last known status and metrics shown in the console) is unchanged; both subscribe to the same device topics.

Each box is its own process and can be scaled on its own. All three ship in the `data-api` image (`src/data-plane/go`) with different entrypoints.

| Service | Command | Ops port (private) | NATS user |
| --- | --- | --- | --- |
| Ingestion | `device-ingest` | 8093 | `ingest` |
| Rule processor | `rule-processor` | 8094 | `rules` |
| ClickHouse writer | `clickhouse-writer` | 8095 | `chwriter` |

Each ops port serves `/metrics`, `/healthz` and `/readyz`. Never publish them.

## Identity: where organization and device come from

An event's organization and device are never read from a payload.

1. The broker authenticates the device (`device_<uuid>`) through the control plane and only lets it publish under `devices/<uuid>/...`.
2. `device-ingest` takes the device id from the topic, and the organization from the control plane's **device directory** (`GET /internal/data-plane/devices`, enrolled and enabled devices only, with `ETag`/`If-None-Match`). It refreshes every `DEVICE_DIRECTORY_INTERVAL` (30 s) and keeps serving the last copy while the control plane is down, for at most `DEVICE_DIRECTORY_MAX_STALE` (15 min). After that it fails closed: messages are held, not accepted.
3. Messages from unknown or disabled devices are rejected (`meteorcloud_device_ingest_rejected_total{reason="unknown_device"}`).
4. `organization_id` or `device_id` fields inside a payload are ordinary data and never change the subject.

Today's agent publishes `metrics` and `status`. The broker topic policy (PR #42) reserves `devices/<id>/events` and denies it to devices until a `device-events` publish rule is added to `DEVICE_RULES` in `app/mqtt/policy.py`; ingestion already handles that topic when it opens.

The ids in a subject are canonical UUIDs, so they can never contain `.`, `*` or `>`.

## Subjects and the event envelope

| MQTT topic (device) | Subject | `kind` | `stream` |
| --- | --- | --- | --- |
| `devices/<id>/metrics` | `telemetry.v1.<org>.<device>` | `telemetry` | `meteor.device.metrics` |
| `devices/<id>/status` | `device.status.v1.<org>.<device>` | `event` | `meteor.device.status` |
| `devices/<id>/events` | `device.events.v1.<org>.<device>` | `event` | `meteor.device.events` |

Subjects are versioned. A breaking envelope change gets new subjects (`telemetry.v2...`), which the stream can capture next to v1 while consumers migrate.

The message body is the data plane's existing versioned envelope ([contract](../../contracts/data-plane-v1.md)), so device events and application events share one schema and one ClickHouse table:

```json
{
  "id": "0193f6c2-8b1e-7c3a-9d4f-2a1b3c4d5e6f",
  "schema_version": 1,
  "kind": "telemetry",
  "type": "device.metrics",
  "stream": "meteor.device.metrics",
  "organization_id": "<org uuid>",
  "device_id": "<device uuid>",
  "time": "2026-11-12T12:00:00Z",
  "received_at": "2026-11-12T12:00:00.412Z",
  "data": { "cpu_percent": 35.2, "memory_percent": 87.5, "temperature_c": 54.1 }
}
```

Field names map to the brief's example as `event_id` → `id`, `event_type` → `kind`/`type`, `timestamp` → `time`, `payload` → `data`. The `meteor.` stream prefix is reserved, so applications cannot publish into device streams through the application API. Headers `Meteor-Organization-Id`, `Meteor-Device-Id` and `Meteor-Schema-Version` let tools route without decoding.

**Payload rules.** A device payload must be a JSON object up to `DEVICE_INGEST_MAX_PAYLOAD_BYTES` (64 KiB). Optional fields:

- `event_id` (or `id`): a device-chosen id, 1-64 characters of `A-Z a-z 0-9 . _ : -`.
- `timestamp` (or `time`), RFC 3339: the measurement time. A timestamp more than 5 minutes in the future or older than 30 days (for example a Raspberry Pi without a real-time clock before NTP sync) is replaced by the receive time and counted in `meteorcloud_device_ingest_timestamp_replaced_total`.

**Event ids.** A device-supplied id is used as is. Otherwise, when the payload has a timestamp (the agent always sends one with metrics), the id is a UUIDv5 of device, channel and the exact payload, so an MQTT redelivery of the same message gets the same id. Without either, a UUIDv7 is minted. The id is also the JetStream `Nats-Msg-Id`.

Invalid messages (not JSON, not an object, too large, bad id, unexpected topic) are counted by reason, logged with organization, device and size but never the payload, and dropped: MQTT gives a device no error channel.

## Stream

`device-ingest` creates or updates the stream at start-up (turn off with `NATS_STREAM_MANAGE=false` when operators manage streams with the nats CLI, NACK or Terraform).

| Variable | Default | Meaning |
| --- | --- | --- |
| `NATS_STREAM_NAME` | `METEOR_DEVICE` | Stream name |
| `NATS_STREAM_SUBJECTS` | `telemetry.v1.>,device.status.v1.>,device.events.v1.>` | Captured subjects |
| `NATS_STREAM_RETENTION` | `limits` | `limits` (every consumer reads independently; keep this), `interest`, `workqueue` |
| `NATS_STREAM_MAX_BYTES` | `10GB` | Size cap (`-1` unlimited; K/M/G/T suffixes) |
| `NATS_STREAM_MAX_AGE` | `168h` | Oldest message kept. This bounds how long a consumer or ClickHouse may be down without losing data |
| `NATS_STREAM_MAX_MSG_SIZE` | `1M` | Largest message |
| `NATS_STREAM_STORAGE` | `file` | `file` or `memory` |
| `NATS_STREAM_REPLICAS` | `1` | `1` for one server, `3` for a three-node cluster |
| `NATS_STREAM_DISCARD` | `old` | `old` drops the oldest when full; `new` refuses publishes, which makes ingestion hold MQTT messages (backpressure) |
| `NATS_STREAM_DUPLICATE_WINDOW` | `2m` | `Nats-Msg-Id` de-duplication window |

## Consumers

Every consumer is a **durable pull consumer** with explicit acknowledgements. Each has its own durable name, so its progress is independent: a stopped rule processor never holds back the writer, and the writer's acks never remove the rule processor's events (retention is `limits`, not `workqueue`).

Settings use a prefix per consumer: `RULES_CONSUMER_*` and `CLICKHOUSE_WRITER_CONSUMER_*`.

| Suffix | Rule processor | ClickHouse writer | Meaning |
| --- | --- | --- | --- |
| `_DURABLE` | `rule-processor` | `clickhouse-writer` | Durable name (the NATS permissions name it, see below) |
| `_FILTER_SUBJECTS` | all three | all three | Comma-separated subject filters |
| `_ACK_WAIT` | `30s` | `1m` | Redelivery after this long without an ack |
| `_MAX_DELIVER` | `10` | `-1` (forever) | Delivery attempts; storage never gives up while the stream still holds the message |
| `_BACKOFF` | none | none | Server-side redelivery delays after ack timeouts, e.g. `5s,30s,2m` |
| `_MAX_ACK_PENDING` | `2000` | `4000` | Unacknowledged messages across all replicas (flow control) |
| `_FETCH_BATCH` | `100` | `500` | Messages per pull |
| `_FETCH_MAX_WAIT` | `1s` | `1s` | Longest wait for a pull |
| `_DELIVER_POLICY` | `all` | `all` | Where a new durable starts: `all` or `new` |

**Rule processor** (`internal/ruleproc`) pulls a batch, spreads it over `RULES_WORKERS` workers by organization and device (one device is always handled by the same worker, in stream order within a replica), and calls a `Handler`:

```go
type Handler interface {
    Handle(ctx context.Context, ev *envelope.Event) error
}
```

`nil` acks. An error naks with a delay from `RULES_RETRY_BACKOFF` (`1s,5s,30s,2m`, by attempt). `ruleproc.Permanent(err)` terminates the message instead of retrying. The default handler counts and logs events; the rule engine plugs in at `newHandler` in `cmd/rule-processor/main.go`. Handlers see at-least-once delivery and must be idempotent on `ev.ID`; `ruleproc.Dedupe` adds an in-process guard.

**ClickHouse writer** (`internal/chwriter`) buffers up to `CLICKHOUSE_WRITER_BATCH_SIZE` (1000) messages or `CLICKHOUSE_WRITER_FLUSH_INTERVAL` (1s), inserts the batch into the `events` table, and only then acks every message in it. Nothing more is pulled while a flush runs, so a slow ClickHouse slows consumption instead of growing memory.

## Failure handling

| Failure | What happens |
| --- | --- |
| JetStream unavailable | `device-ingest` retries the publish with backoff and does **not** ack the MQTT message. The broker stops sending once `DEVICE_INGEST_INFLIGHT` (MQTT Receive Maximum, 512) messages are unacknowledged. Clients reconnect forever (`NATS_MAX_RECONNECTS=-1`) |
| Control plane unavailable | Ingestion serves the last device directory for up to 15 minutes, then holds messages (fails closed) |
| ClickHouse unavailable | The writer retries the batch for `CLICKHOUSE_WRITER_RETRY_FOR` (20s), keeping the messages' ack timers alive, then naks them with `CLICKHOUSE_WRITER_RETRY_DELAY` (10s). Nothing is acked or lost; the stream's `MAX_AGE`/`MAX_BYTES` bound the outage |
| Handler error | Nak with backoff; `term` for permanent errors or after `MAX_DELIVER` attempts |
| Undecodable message | Terminated (never decodes), logged with its subject |
| Crash mid-batch | Unacked messages are redelivered after `ACK_WAIT` |
| Duplicates | Same id within the duplicate window: dropped by the stream. Later redeliveries: the writer skips ids already stored (per organization); the rule handler is idempotent on the id |
| Shutdown (SIGTERM) | Ingestion unsubscribes, finishes in-flight messages (`DEVICE_INGEST_DRAIN_TIMEOUT`, 20s) and leaves the rest unacknowledged for redelivery. Consumers finish and ack the batch in progress; the writer flushes its buffer. Then the NATS connection is drained |

Device telemetry is MQTT QoS 1 between the agent and the broker, and MQTT shared subscriptions are not durable storage. The durability boundary is the JetStream publish acknowledgement: everything before it relies on MQTT redelivery, everything after it on JetStream.

## Security

- **No device access.** NATS is reachable only on the internal network. Compose publishes the client and monitoring ports on `127.0.0.1` only, for the nats CLI on the host. Never expose `8222` (monitoring) or `4222` publicly.
- **One user per service** ([`nats.conf`](../../deploy/compose/config/nats/nats.conf)), passwords from the environment (`NATS_INGEST_PASSWORD`, `NATS_RULES_PASSWORD`, `NATS_CHWRITER_PASSWORD`, `NATS_ADMIN_PASSWORD`; `*_FILE` variants are read by the services). The NATS container refuses to start with an empty password.
  - `ingest` may publish device subjects and manage the `METEOR_DEVICE` stream. It cannot subscribe to events or create consumers.
  - `rules` and `chwriter` may only create, read, pull from and ack **their own** durable consumer. They cannot publish events or touch each other's consumer.
  - Each service uses its own inbox prefix (`_INBOX_<service>`) and may only subscribe there, so no service can read another's replies.
  - `admin` is for operators (nats CLI) only.
  - The test `TestNATSConfigPermissions` checks all of this against the shipped file.
- **Tenants.** Services are trusted platform components; tenants never get NATS credentials. Tenant isolation comes from identity binding at ingestion (above). Because the organization is a subject token, a future per-tenant consumer can filter on `telemetry.v1.<org>.>`.
- **TLS.** Set `NATS_URL=tls://...`, `NATS_TLS_CA_FILE`, and for mutual TLS `NATS_<SERVICE>_TLS_CERT_FILE`/`_TLS_KEY_FILE`. Decentralized auth (JWT + nkeys) works through `NATS_<SERVICE>_CREDS_FILE` instead of user and password.
- Changing a durable name means changing its permissions in `nats.conf`.

## Observability

All services expose Prometheus metrics on their ops port (scraped by the bundled Prometheus through DNS discovery when the `nats` profile runs). Labels are bounded: never an organization, device or event id. Those go to the structured JSON logs instead, with organization, device and event ids on every processing failure and never the payload or credentials.

| Metric | Meaning |
| --- | --- |
| `meteorcloud_nats_connected` | 1 while connected |
| `meteorcloud_nats_reconnects_total`, `_disconnects_total` | Connection churn |
| `meteorcloud_nats_published_total{subject_root,result}` | Publishes: `ok`, `duplicate`, `error` |
| `meteorcloud_nats_consumed_total{consumer}` | Messages delivered |
| `meteorcloud_nats_processed_total{consumer,result}` | `ack`, `nak` (failure, retried), `term` |
| `meteorcloud_nats_redeliveries_total{consumer}` | Retries seen |
| `meteorcloud_nats_consumer_pending_messages{consumer}` | Backlog (also `_ack_pending_`, `_redelivered_`) |
| `meteorcloud_nats_processing_duration_seconds{consumer}` | Time per batch |
| `meteorcloud_nats_end_to_end_latency_seconds{consumer}` | Stored in stream to acked |
| `meteorcloud_nats_batch_size{consumer}` | Messages per batch |
| `meteorcloud_device_ingest_received_total`, `_rejected_total{reason}`, `_published_total` | Ingestion |
| `meteorcloud_clickhouse_writer_insert_failures_total`, `_rows_total{result}`, `_insert_duration_seconds` | Writer |
| `meteorcloud_rule_processor_events_total{kind}` | Default rule handler |

## Local development

Add `nats` (and `clickhouse`) to `COMPOSE_PROFILES` in `deploy/compose/.env` (`.env.example` has development passwords), then `make dev`, or:

```bash
cd deploy/compose
docker compose --profile nats --profile clickhouse up -d --wait nats clickhouse device-ingest rule-processor clickhouse-writer
```

NATS stores data in the `nats_data` volume. `NATS_PORT` and `NATS_MONITOR_PORT` change the loopback ports.

Send telemetry from a device (or a simulated one): an enrolled agent publishes metrics every interval by itself, or on demand:

```bash
meteorcli mqtt-test metrics '{"cpu_percent": 35.2, "memory_percent": 87.5, "temperature_c": 54.1}'
```

Then:

```bash
# ingestion and consumers
curl -s localhost:8093/metrics | grep meteorcloud_device_ingest_published_total   # only from inside the network,
docker compose logs -f device-ingest rule-processor clickhouse-writer            # or publish the ops ports locally

# the stored rows
docker compose exec clickhouse clickhouse-client --user meteorcloud --password dev-clickhouse \
  -q "SELECT stream, device_id, count() FROM meteorcloud.events WHERE stream LIKE 'meteor.device.%' GROUP BY 1, 2"

# stop a consumer, send more, start it again: it resumes where it stopped
docker compose stop rule-processor && docker compose start rule-processor
```

Inspect streams and consumers with the NATS CLI (here from the `natsio/nats-box` image on the Compose network, as the `admin` user):

```bash
docker run --rm -it --network meteorcloud -e NATS_URL=nats://nats:4222 \
  -e NATS_USER=admin -e NATS_PASSWORD=dev-nats-admin natsio/nats-box
nats stream info METEOR_DEVICE
nats consumer ls METEOR_DEVICE
nats consumer info METEOR_DEVICE clickhouse-writer
nats stream get METEOR_DEVICE --last-for 'telemetry.v1.>'
nats stream view METEOR_DEVICE --subject 'telemetry.v1.<org>.>'
```

From the host, the CLI reaches `nats://127.0.0.1:4222` and monitoring is at `http://127.0.0.1:8222/jsz?consumers=true`.

Tests (`make data-api-test`) run a real JetStream server embedded in the test process, so they need no Docker: publishing, consuming, acks, consumer and server restart, duplicates, temporary failures, ClickHouse outage, independent progress, invalid messages, tenant isolation and the shipped NATS permissions.

## Production

- Run a **three-node** JetStream cluster and set `NATS_STREAM_REPLICAS=3`. Each server needs a unique `server_name`, a `cluster { name, listen, routes [...] }` block pointing at the other two, TLS on client and route connections, and its own persistent volume. Point services at all nodes: `NATS_URL=tls://nats-1:4222,tls://nats-2:4222,tls://nats-3:4222`. Nothing in the services assumes a node count.
- Size `NATS_STREAM_MAX_AGE`/`MAX_BYTES` for the longest outage of ClickHouse or a consumer you want to absorb.
- Scale `device-ingest` replicas freely (shared subscription), and each consumer service freely (replicas share the durable). Across replicas, per-device order is not guaranteed; consumers rely on event time and ids.
- Keep monitoring endpoints private; scrape them from inside the network.
- Generate real passwords (or nkeys/JWT credentials) per service and keep them out of the repository.

## Add another consumer

1. Pick a durable name and subject filters (for example `alerts-export` on `telemetry.v1.>`).
2. Add a NATS user in `nats.conf` with the same permission block as `rules`, with the durable name and inbox prefix replaced, and a `NATS_<NAME>_PASSWORD`.
3. Write a command that loads `natsjs.LoadConnConfig(e, "<name>", "NATS_<NAME>")` and `natsjs.LoadConsumerConfig(e, "<NAME>_CONSUMER", defaults)`, then calls `service.RunConsumer` with a function that consumes batches, either through `ruleproc.Processor` with your own `Handler`, or `natsjs.Batches` directly. Ack only after the work is durable.
4. Add a Compose service and a Prometheus scrape job like the existing ones.

The new consumer starts at the beginning of the stream (or `_DELIVER_POLICY=new`) and never affects the others.
