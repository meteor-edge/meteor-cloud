# Device telemetry history

The console shows CPU, memory, disk, temperature and load charts for every device (device page, **Overview** tab, **History**), for the last 1 hour, 6 hours, 24 hours, 7 days or 30 days. The history lives in ClickHouse. PostgreSQL keeps only the latest snapshot (the **Live resources** meters), as before.

## Path

```text
meteorcli ── MQTT devices/<id>/metrics (every 60 s) ──▶ broker
                                                          │
                                       device-ingest (identity from the topic + device directory)
                                                          │
                                  NATS JetStream  telemetry.v1.<org>.<device>
                                                          │
                                          clickhouse-writer (batched, acked after insert)
                                                          │
                                  ClickHouse events  (stream = meteor.device.metrics)
                                                          │  materialized view
                                  ClickHouse device_metrics (one row per numeric key)
                                                          │
             data API  GET /internal/v1/organizations/{org}/devices/{device}/metrics  (ops port 8092)
                                                          │
             control plane  GET /api/v1/organizations/{org}/devices/{id}/telemetry?range=24h  (checks device.read)
                                                          │
                                                   console charts
```

Devices never reach NATS or ClickHouse. The console never reaches the data plane: the control plane authorizes the request (same permission as reading the device) and forwards it with the shared internal token.

## Storage

`device_metrics` is filled by a materialized view on `events`. Every numeric top-level key of a metrics payload becomes a row `(organization_id, device_id, name, ts, event_id, value)`, so:

- a newer agent that sends a new key (for example `load_1m`) is stored without a schema change;
- strings, `null`, objects and arrays are skipped; at most 160 keys of at most 64 characters per message are kept (enough for the system keys plus 20 apps);
- charts read a narrow table sorted by `(organization_id, device_id, name, ts)` and never parse JSON.

The table is a `ReplacingMergeTree` keyed on the event id, which folds duplicates from the one-time backfill or from two writer replicas. Until a merge has run, the chart query keeps one sample per event id itself, so a duplicate does not skew avg or count.

When the view does not exist yet (first start after upgrading), the data API or the ClickHouse writer copies the metrics already in `events`, creates the view, then copies again whatever arrived in between. A failed first copy is retried at the next start (the view does not exist yet); the catch-up copy is recorded in `device_metrics_backfill` and retried at every start until it has succeeded once.

Retention follows `DATAPLANE_RETENTION_DAYS` on both tables (a table TTL; 0 keeps everything).

At one message per minute a device produces about 1,440 events and 6,000 to 17,000 metric rows a day. ClickHouse compresses them (Gorilla + ZSTD on the values) to a few hundred kilobytes per device per month.

## Query API

Internal (ops listener, `X-Data-Plane-Internal-Token`):

```http
GET /internal/v1/organizations/{org}/devices/{device}/metrics
    ?start=2026-11-14T00:00:00Z&end=2026-11-14T12:00:00Z&bucket=15m
    &names=cpu_percent,memory_percent,disk_percent,temperature_c
```

```json
{"organization_id": "...", "device_id": "...", "start": "...", "end": "...", "bucket_seconds": 900,
 "series": [{"name": "cpu_percent", "points": [{"t": "2026-11-14T00:00:00Z", "avg": 12.5, "min": 3, "max": 40, "count": 15}]}]}
```

Defaults: the last hour, `1m` buckets, the four metrics every agent sends. Up to 16 names; the bucket count is bounded by `DATAPLANE_QUERY_MAX_BUCKETS`. Buckets without samples are left out, so a gap means the device did not report. With `DATAPLANE_STORAGE=postgres` or `memory` the same endpoint works through the generic aggregate (slower, fine for small installations), but data only arrives there through the HTTP API; the device path writes to ClickHouse.

Console-facing (control plane): `GET /api/v1/organizations/{org}/devices/{id}/telemetry?range=1h|6h|24h|7d|30d`. The range picks the bucket: 1m, 5m, 15m, 1h, 6h. Without `DATA_PLANE_API_URL` and `DATA_PLANE_INTERNAL_TOKEN` it answers 503 `telemetry_unavailable` and the console says the feature is not enabled.

## What the agent sends

| Key | Since | Meaning |
| --- | --- | --- |
| `cpu_percent` | all | Busy CPU over a 0.1 s sample |
| `memory_percent` | all | `MemTotal - MemAvailable` over `MemTotal` |
| `disk_percent` | all | Used space of `/`, as `df` shows it |
| `temperature_c` | all | SoC temperature, `null` when the board has no sensor |
| `memory_used_bytes`, `memory_total_bytes` | 0.3.4 | Same reading in bytes |
| `disk_used_bytes`, `disk_total_bytes` | 0.3.4 | Same reading in bytes |
| `load_1m`, `load_5m`, `load_15m` | 0.3.4 | Load average |
| `uptime_seconds` | 0.3.4 | Seconds since boot |

| `net_rx_bps`, `net_tx_bps` | 0.3.4 | Bytes per second over physical interfaces since the last report |
| `wifi_signal_dbm` | 0.3.4 | Wi-Fi signal level, when the device has Wi-Fi |
| `agent_errors` | 0.3.4 | Errors the agent logged since the last report |
| `app.<name>.cpu`, `.mem`, `.restarts`, `.up`, `.healthy`, `.health_ms` | 0.3.4 | Per Docker container; see [platform history](platform-history.md) |

Older agents keep working; the Load chart appears once a device runs 0.3.4. App lifecycle events, online/offline changes, deployment history and saved warnings are described in [platform history](platform-history.md).

## Requirements

The history needs the NATS device path and ClickHouse: Compose profiles `nats` and `clickhouse` (plus `data-api`), Ansible `nats_enabled: true` and `dataplane_storage: clickhouse`, or Helm `nats.enabled`, `clickhouse.enabled` and `dataApi.enabled`. See [device event path](jetstream.md#deploy).
