# Capacity and load testing

How many devices one MeteorCloud API handles, how it was measured, and how to measure
your own installation. Tracking issue: [#68](https://github.com/meteor-edge/meteor-cloud/issues/68).

## What the API does per device

Every agent sends one heartbeat per `DEVICE_HEARTBEAT_INTERVAL_SECONDS` (60 s by default).
A heartbeat authenticates the device, stores inventory, metrics and capabilities, and
returns whether actions, Wi-Fi changes or desired software are pending. So the steady
load is roughly:

    heartbeats per second = devices / heartbeat interval

10,000 devices at the default interval send about 167 heartbeats per second. MQTT
traffic, deployments and console use come on top, but heartbeats are the floor that
grows with the fleet.

## Measured (2026-11)

One machine with 4 CPUs and 16 GB RAM ran PostgreSQL 16, the API and the load generator
together. The generator uses CPU too, so a dedicated API host does better. Settings:
`CACHE_PROVIDER=memory`, `MQTT_ENABLED=false`, default pool (5 + 10 per worker). Each
heartbeat carries full inventory, capabilities and metrics, like `meteorcli`.

| Workers | Load | Heartbeats/s handled | p50 | p95 | Errors |
|---------|------|----------------------|-----|-----|--------|
| 1 | 1,000 devices, every 60 s | 16.7 (all) | 24 ms | 39 ms | 0 |
| 1 | 10,000 devices, every 60 s | 73 (of 167) | 833 ms | 1.3 s | 0 |
| 4 | 10,000 devices, every 60 s | 166.5 (all) | 35 ms | 254 ms | 0 |
| 4 | forced 250/s and 350/s | about 200 | 310 ms | 520 ms | 0 |

Read as a rule of thumb: **one API worker process handles about 70 heartbeats per second,
about 4,000 devices at the default 60 s interval.** The API was CPU bound (one core per
worker); PostgreSQL used little CPU (under 10% per connection at 100 heartbeats per second). Above its limit the
API queues requests and answers slowly, but answers all of them.

Before the fixes in the same change, 10,000 devices froze a single worker within a minute:
see below.

## Scaling

- **More replicas** (Helm `backend.replicaCount`, or more API containers behind the proxy)
  is the first choice. Each replica is a separate Prometheus target, and the deploy sweeper
  and history relay already coordinate through PostgreSQL advisory locks.
- **More worker processes per replica** (Helm `backend.workers`, Ansible `backend_workers`,
  Compose `WEB_CONCURRENCY`) uses more cores in one pod or VM. The image command and
  production Compose pass `--workers` to uvicorn when that value is greater than 1.
  `make dev` stays one process with `--reload` and ignores `WEB_CONCURRENCY`.
  Two things work per process, not per pod, so prefer replicas while they matter to you:
  - `/metrics` answers for the one worker that took the scrape, so counters jump between
    scrapes.
  - The console's MQTT test page only sees messages that reached the same worker.
- **PostgreSQL connections.** Each worker keeps its own pool. The app reads
  `DB_POOL_SIZE` (5), `DB_MAX_OVERFLOW` (10), `DB_POOL_TIMEOUT_SECONDS` (30) and
  `DB_POOL_RECYCLE_SECONDS` (1800). Helm sets the first two from `backend.dbPool`;
  Ansible sets them from `db_pool_size` and `db_max_overflow`. Keep
  `workers x replicas x (DB_POOL_SIZE + DB_MAX_OVERFLOW)` under PostgreSQL's
  `max_connections` (100 by default), or put PgBouncer in front and lower
  `DB_POOL_RECYCLE_SECONDS` to suit it.
- **Longer heartbeat interval.** `DEVICE_HEARTBEAT_INTERVAL_SECONDS=120` halves the load;
  raise `DEVICE_OFFLINE_THRESHOLD_SECONDS` with it.

## Fixed while measuring

- **Freeze under a burst of heartbeats.** FastAPI runs the device-auth dependency and the
  endpoint as separate calls on its 40-thread pool. Auth held its database connection while
  waiting for the second call, so once more than 15 heartbeats were in flight every thread
  waited for a connection that only a waiting request could give back. The API stopped
  answering, even `/health`, and stayed that way while heartbeats kept coming. Device auth now returns the
  connection before the endpoint runs.
- **Identity lookup on every heartbeat.** Each heartbeat checked that its serial number
  and MAC addresses were not used by another device. That lookup is global, so the
  `(organization_id, serial_number)` index does not serve it, and MAC addresses are not
  indexed. The check now runs only when the identity changed.

## Run it yourself

`scripts/loadtest/fleet_load.py` needs only Python 3.11+. It registers devices through
the real agent API and then sends heartbeats spread evenly over the interval. One machine
plays thousands of devices. A real device runs [meteorcli](meteorcli.md); this script
only speaks the heartbeat HTTP API.

Never point it at a production installation: it creates an organization and thousands of
devices. Use a staging copy, and raise `REGISTRATION_RATE_LIMIT_REQUESTS` there for the
setup step, since one IP registers every device.

```bash
# once: an organization, a registration token and N device credentials (file is 0600)
python scripts/loadtest/fleet_load.py setup --server https://staging.example.com \
    --email ops@example.com --password '...' --devices 10000 --out devices.json

# steady state: every device every 60 s for 5 minutes
python scripts/loadtest/fleet_load.py run --server https://staging.example.com \
    --devices-file devices.json --interval 60 --duration 300

# find the ceiling: force a total rate
python scripts/loadtest/fleet_load.py run --server https://staging.example.com \
    --devices-file devices.json --rate 300 --duration 60 --json result.json
```

The summary shows the target and achieved rate, latency percentiles and errors.
`client_lag_p95_ms` is how long heartbeats waited inside the generator: if it grows while
`achieved_rps` falls short, the API is saturated (or raise `--concurrency` if the API is
idle). Delete the organization afterwards to remove the devices.
