# Platform history

Besides the CPU, memory and disk charts ([device telemetry](device-telemetry.md)), ClickHouse keeps the history that answers "what happened to this device and when":

| What | Stream | Written by | Shown in the console |
| --- | --- | --- | --- |
| Online/offline changes | `meteor.device.status` | agent (online on connect) and the broker (last will: offline) | Device page, Overview tab: availability strip with uptime % and how often it went offline |
| Devices online over time | `meteor.device.metrics` (distinct devices per bucket) | agent, every 60 s | Organization overview: **Devices online** |
| Deployment status changes | `meteor.deployments` | control plane (outbox) | Device page, Software tab: **Deployment history** |
| App lifecycle (started, exited with exit code, OOM kill, restarted, crash loop, health check failing/healthy, removed) | `meteor.device.events` (`app.*` types) | agent | Device page, Overview tab: **Apps** (recent changes) |
| Per-app CPU, memory, restarts, health and health-check latency | `meteor.device.metrics` (`app.<name>.cpu`, `.mem`, `.restarts`, `.up`, `.healthy`, `.health_ms`) | agent | **Apps** table (latest values); full history in `device_metrics` |
| Network rate, Wi-Fi signal, agent errors | `meteor.device.metrics` (`net_rx_bps`, `net_tx_bps`, `wifi_signal_dbm`, `agent_errors`) | agent | stored for charts and rules; no chart yet |
| System warnings (journal entries at warning or worse) | `meteor.device.logs` | agent, through the root `meteorcli-logs warnings` helper | Device page, Access tab: **Saved warnings**, searchable (needs `device.logs`) |

The console only charts what helps to understand a device quickly: one fleet chart (devices online), one strip per device (availability) and tables for the rest.

## Device side

The agent (0.3.4) publishes, besides `status` and `metrics`:

- `devices/<id>/events`: one message per app change or journal warning, `{"type": "app.exited", "app": "web", "exit_code": 137, "oom_killed": true, "timestamp": "..."}` or `{"type": "log", "message": "...", "priority": 3, "unit": "smartd.service", "source": "smartd", "timestamp": "..."}`. Not retained. The broker lets a device publish only its own events topic.
- extra numeric keys in the metrics payload (see the table above). The `device_metrics` view keeps every numeric key, so they need no schema change.

Apps are the Docker containers on the device (at most 20; a name longer than 50 characters is shortened to 41 characters plus a short hash in metric keys, so every key stays under 64 and two long names never collide; events keep the full name). The agent compares two looks one interval apart: the first look after a start only sets the baseline. A crash loop is three restarts within ten minutes, reported once until the container calms down. Journal warnings are read after the last cursor, at most 200 per minute, each message cut to 1,000 characters; the helper prints only message, priority, unit, identifier, time and cursor, never other journal fields.

device-ingest stores events with `type: "log"` in `meteor.device.logs` instead of `meteor.device.events`, so a noisy journal cannot crowd out app lifecycle events. Both go to JetStream on `device.events.v1.<org>.<device>`, so the rule processor sees them too.

## Deployment history (control plane outbox)

Every status change of a device in a Deploy (pending, downloading, installing, succeeded, failed, timed_out, cancelled, rolled back, ...) is written to the `platform_event_outbox` table by a SQLAlchemy `before_flush` hook, in the same transaction as the change, wherever in the code it happens. A relay in each API replica (one at a time, Postgres advisory lock) sends the rows to the data API every `HISTORY_RELAY_INTERVAL_SECONDS` (5 s) and deletes them once stored. The row id is the event id, so a batch sent twice is stored once. While the data API is down the outbox grows; rows older than 7 days are dropped. Nothing is recorded when `DATA_PLANE_API_URL` or `DATA_PLANE_INTERNAL_TOKEN` is unset.

Each event carries `deployment_id`, `release_id`, `status`, `previous_status`, `activated_at`, `seconds` (from activation to a terminal status) and `error` for failures. That is enough for failure rate per release, slow devices and rollout timelines.

## Offline notifications (later)

Notifications are planned in issue #54. The data is already there: the broker publishes the device's last will (`{"status": "offline"}`) on `devices/<id>/status` when the connection drops, device-ingest puts it on JetStream subject `device.status.v1.<org>.<device>`, and the rule processor consumes that subject. A notification service can subscribe to the same subject (or a rule can match `status = offline`), wait a grace period for a reconnect, and send email, Telegram or Slack messages. The history in `meteor.device.status` gives "offline since" and how often a device flaps.

## APIs

Internal (data API ops listener `:8092`, header `X-Data-Plane-Internal-Token`):

```http
POST /internal/v1/events
{"events": [{"id": "<uuid>", "organization_id": "...", "device_id": "...", "stream": "meteor.deployments",
             "type": "deployment.device.failed", "time": "...", "data": {...}}]}
-> {"received": 1, "inserted": 1}

GET /internal/v1/organizations/{org}/events?stream=meteor.device.status&device_id=&start=&end=
    &order=asc|desc&limit=&cursor=&filter={"deployment_id":"..."}&q=text&previous=1
-> {"stream": "...", "events": [...], "next_cursor": null, "previous": {...}}

GET /internal/v1/organizations/{org}/devices/online?start=&end=&bucket=5m
-> {"organization_id": "...", "start": "...", "end": "...", "bucket_seconds": 300, "buckets": [{"t": "...", "devices": 12}]}
```

Only `meteor.deployments` can be written; any `meteor.*` stream can be read. `q` matches the `message` field, ignoring case. `previous=1` also returns the last event before `start` (the state the availability strip starts in).

Console-facing (control plane, `/api/v1/organizations/{org}`):

| Endpoint | Permission |
| --- | --- |
| `GET /fleet/online-history?range=24h` | `device.read` on the whole organization (403 `fleet_scope_required` for members limited to device groups) |
| `GET /devices/{id}/availability?range=24h` | `device.read` |
| `GET /devices/{id}/deployment-history?limit=50` | `device.read` |
| `GET /devices/{id}/apps` | `device.read` |
| `GET /devices/{id}/log-history?range=7d&q=&cursor=` | `device.logs` |

Without the data API they answer 503 `telemetry_unavailable`.
