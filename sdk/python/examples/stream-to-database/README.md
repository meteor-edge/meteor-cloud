# Stream MeteorCloud data into your own database

A small, production-shaped SDK application that copies telemetry, events or inference results from MeteorCloud into a PostgreSQL (or SQLite) database **you** own and operate. Your devices keep publishing with the SDK as usual. This process reads the data back out and writes every event exactly once into a table you can query, join and keep as long as you like.

```
 device app ──publish──▶ MeteorCloud data API ──live + history──▶ sink.py ──INSERT … ON CONFLICT──▶ your PostgreSQL
```

**Client-side vs. server-side.** This is the *client-side* path: you run the code, with an ordinary application token, wherever you want (next to your database, on a VM, in your own Kubernetes). It works today with nothing but the SDK. MeteorCloud is also planning *server-side* data export connectors (configured in MeteorCloud and run by the platform, for Postgres, webhooks, Kafka, S3 and more). Use this example when you want full control over the code and the schema, or until the connector you need exists.

## 5-minute quickstart

### A. Try it on your laptop, no MeteorCloud install needed

You need Python 3.10+, Go (to build the data API) and a PostgreSQL. From the repository root:

```bash
# 1. The data API as one binary, in memory, with two applications: a meter that publishes and a sink that reads.
(cd src/data-plane/go && go build -o /tmp/dataplane ./cmd/dataplane)
h() { printf %s "$1" | sha256sum | cut -d' ' -f1; }
cat > /tmp/policy.json <<EOF
{"applications": [
  {"id": "app-meter", "organization_id": "org-1", "slug": "meter",
   "permissions": {"publish": ["energy.**"], "subscribe": [], "read": [], "receive_commands": false, "send_commands": []}},
  {"id": "app-sink", "organization_id": "org-1", "slug": "warehouse-sink",
   "permissions": {"publish": [], "subscribe": ["energy.**"], "read": ["energy.**"], "receive_commands": false, "send_commands": []}}],
 "credentials": [
  {"id": "c1", "organization_id": "org-1", "application_id": "app-meter", "device_id": "dev-1", "environment": "dev",
   "token_hash": "$(h mca_meter_dev)", "expires_at": "2099-01-01T00:00:00Z"},
  {"id": "c2", "organization_id": "org-1", "application_id": "app-sink", "device_id": null, "environment": "dev",
   "token_hash": "$(h mca_sink_dev)", "expires_at": "2099-01-01T00:00:00Z"}]}
EOF
DATAPLANE_STORAGE=memory DATAPLANE_BUS=memory DATAPLANE_POLICY_FILE=/tmp/policy.json /tmp/dataplane &

# 2. Install the SDK and the PostgreSQL driver.
cd sdk/python/examples/stream-to-database
python -m venv .venv && . .venv/bin/activate
pip install ../.. -r requirements.txt

# 3. Start the sink against your database (any database you can reach; it creates its tables).
export METEORCLOUD_ENDPOINT=http://localhost:8082 METEORCLOUD_TOKEN=mca_sink_dev
export SINK_DATABASE_URL=postgresql://postgres@localhost:5432/postgres
#      or, with no PostgreSQL at hand:  SINK_DATABASE_URL=sqlite:///./events.db
export SINK_STREAMS=energy.metrics SINK_SWEEP_INTERVAL=5s
python sink.py &

# 4. Publish some readings as a device would, then look at your table.
METEORCLOUD_TOKEN=mca_meter_dev python fake_meter.py 30 0.1
psql postgresql://postgres@localhost:5432/postgres \
  -c "select time, device_id, data->>'power' as power from meteor_events order by time desc limit 5"
```

Stop the sink with Ctrl+C (or `kill -TERM`): it closes its subscriptions, writes what is queued and exits. Publish more while it is stopped and start it again: the first sweep stores what it missed.

### B. Against your MeteorCloud, with Docker Compose

1. In MeteorCloud, create an application for the sink (for example `warehouse-sink`) with **subscribe** and **read** on the streams you want, such as `energy.**`. It needs no publish permission. Create a cloud credential for it (no device) and save the token as `sink.token` in this folder.
2. `cp .env.example .env` and set `METEORCLOUD_ENDPOINT` (your data API, `https://…`) and `SINK_STREAMS`.
3. `docker compose up --build`. This starts a PostgreSQL 16 for you (`localhost:5433`, user `sink`, database `telemetry`) and the sink.

To use a database you already run, drop the `db` service and set `SINK_DATABASE_URL` in `.env`.

## Configuration

The SDK reads `METEORCLOUD_ENDPOINT` and `METEORCLOUD_TOKEN_FILE` (or `METEORCLOUD_TOKEN`); see the [SDK README](../../README.md#configuration). The sink adds:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SINK_DATABASE_URL` | required | `postgresql://user:password@host:5432/db` or `sqlite:///path/to/file.db` |
| `SINK_STREAMS` | required | Comma separated stream names or patterns: `energy.metrics`, `energy.*`, `energy.**` |
| `SINK_KINDS` | `telemetry` | Any of `telemetry`, `events`, `inference` |
| `SINK_BACKFILL` | `1h` | On the first start, how much history to copy for each named stream |
| `SINK_SWEEP_INTERVAL` | `60s` | How often the sweep reads history to fill gaps |
| `SINK_OVERLAP` | `10m` | Each sweep re-reads this much before its last checkpoint, to pick up data that arrived late |
| `SINK_SETTLE` | `5s` | The sweep stops this far before now, so it never checkpoints past a write still in flight |
| `SINK_BATCH_SIZE` | `500` | Events per database transaction |
| `SINK_FLUSH_INTERVAL` | `1` | Seconds the writer waits to fill a batch |
| `SINK_QUEUE_SIZE` | `10000` | Live events held in memory before the sink skips them and lets the sweep catch up |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows every batch |

Durations accept `30s`, `5m`, `2h`, `1d`.

## What lands in your database

```sql
CREATE TABLE meteor_events (
    organization_id text        NOT NULL,
    event_id        text        NOT NULL,   -- MeteorCloud's message id
    kind            text        NOT NULL,   -- telemetry | event | inference
    stream          text        NOT NULL,   -- e.g. energy.metrics
    type            text        NOT NULL,
    time            timestamptz NOT NULL,   -- when it happened (set by the publisher)
    received_at     timestamptz,            -- when MeteorCloud stored it
    application     text,                   -- publishing application
    device_id       text,                   -- publishing device, null for cloud apps
    environment     text,
    seq             bigint,
    data            jsonb       NOT NULL,   -- the payload
    stored_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, event_id));

CREATE TABLE meteor_sink_checkpoints (kind text, stream text, complete_through timestamptz, PRIMARY KEY (kind, stream));
```

The sink creates both tables on start. It only ever inserts, so you can add your own indexes, views, triggers or a column-typed copy (`CREATE MATERIALIZED VIEW power AS SELECT time, device_id, (data->>'power')::float …`).

## How it works

* **Live.** One subscription per entry in `SINK_STREAMS` delivers new events within milliseconds. The callback only puts them on an in-memory queue, because blocking the SDK's network thread would make MeteorCloud drop the connection as a slow consumer. A writer thread drains the queue in batches.
* **Sweep.** Every `SINK_SWEEP_INTERVAL` the sweep reads each stream's history with `query_all`, from its checkpoint minus `SINK_OVERLAP` to now minus `SINK_SETTLE`, writes what is missing and moves the checkpoint, **in the same transaction**. This is what makes the copy complete: it recovers events published while the sink was stopped, while the live connection was reconnecting, while your database was down, or that were skipped because the queue was full.
* **Exactly once in your table.** Every insert is `ON CONFLICT (organization_id, event_id) DO NOTHING`. An event that arrives live and through the sweep, or twice after a crash, is stored once. You can run a second replica for availability; it only does duplicate work.
* **Retries.** Database errors reconnect and retry with exponential backoff and jitter (0.5 s doubling to 30 s). MeteorCloud calls are retried by the SDK, and a failed sweep is retried with the same backoff.
* **Graceful shutdown.** SIGTERM or Ctrl+C closes the subscriptions, writes what is queued and exits. If the database is unreachable at that moment, the queued events are not lost: the checkpoint only moves after the sweep has written its window, so the next start stores them.
* **Patterns.** History can only be queried per stream name, so a pattern such as `energy.*` is swept for every concrete stream it has delivered live at least once. List important streams by name to also backfill them on the first start.

## Verified

`python -m pytest -q test_sink.py` covers the store, dedup, checkpoints and the full-queue path offline (SQLite, no network). End to end, against the data API binary from this repository with PostgreSQL 16: history published before the first start was backfilled, live events arrived within the flush interval, events published while the sink was stopped were stored after restart, and a PostgreSQL restart in the middle of publishing was retried and caught up. The table ended with exactly the events the data API held, no duplicates.

## Limits of the SDK today

This example uses only what the SDK offers now. These gaps are why it needs a sweep, and what a future durable consumer in the SDK would remove:

1. **No durable consume.** A live subscription does not replay events missed while disconnected, and there is no server-side cursor or acknowledgement. The sweep re-reads `SINK_OVERLAP` of history on every pass to compensate, which costs read traffic.
2. **History is ordered and filtered by event `time`, not by arrival.** A publisher may set `time` up to 30 days in the past. Data that arrives later than `SINK_OVERLAP` behind its own `time` *and* is also missed live (sink stopped, queue full) is not found by the sweep. Raise `SINK_OVERLAP` if your devices buffer offline and publish old readings. A cursor by `received_at` would close this.
3. **No list of streams.** A pattern subscription cannot be backfilled until its first live event reveals the stream name.
4. **Only SDK data.** The sink sees what applications publish through the SDK and data API. Device status and metrics from the `meteorcli` agent use a different path and are not in these streams.
