# Application data plane

The data plane lets applications publish data, subscribe to live data, query history and exchange commands. Applications use the [Python SDK](../../sdk/python/README.md) and never see a broker, a database or a topic.

| I want to | Read |
| --- | --- |
| Write an application | [SDK guide](../../sdk/python/README.md) |
| Register applications, permissions and credentials | [Applications and credentials](applications.md) |
| Run, configure, monitor and troubleshoot it | [Operations](operations.md) |
| Write a client in another language, or use MQTT directly | [Protocol contract](../../contracts/data-plane-v1.md) · [OpenAPI](../../src/data-plane/go/api/openapi.yaml) |

## Try it

```bash
make dev        # the data-api profile is on in deploy/compose/.env.example
```

1. Sign in to the API as an owner and create an application with permissions and a credential ([how](applications.md#quick-start)).
2. Save the token to a file and run an SDK example:

   ```bash
   export METEORCLOUD_ENDPOINT=http://localhost:8082
   export METEORCLOUD_TOKEN_FILE=./my-app.token
   pip install ./sdk/python && python sdk/python/examples/quickstart.py
   ```

To check a whole installation, `python scripts/data-plane-e2e.py` runs the full developer workflow: it registers a device and two applications, deploys a Docker release bound to an application, runs the real agent, and verifies publish, subscribe, history, commands, token rotation, revocation, tenant isolation and the audit trail.

## Architecture

```
 application (device)            application (cloud)             operator / console
  SDK ── HTTPS + WebSocket ─┐   ┌─ SDK ── HTTPS + WebSocket            │
                            ▼   ▼                                      ▼
                  ┌────────────────────────┐   policy (pull)   ┌───────────────┐
                  │   data API (Go)        │ ◀──────────────── │ control plane │
                  │  auth · ingest · live  │ ◀── commands ──── │  (Python)     │
                  │  query · commands      │                   └───────────────┘
                  └──────┬──────────┬──────┘
             fan-out bus │          │ storage
                         ▼          ▼
                      EMQX      ClickHouse (data) + PostgreSQL (commands)
                                             (optional: MQTT clients ─▶ EMQX)
```

- **Control plane** owns organizations, devices, applications, permissions and credentials. It is the only place they are changed, and every change is in the audit log.
- **Data API** (`src/data-plane/go`, one Go binary) authenticates applications, validates and stores messages, fans them out to live subscribers, answers queries and delivers commands. It is deployed and scaled on its own.
- **No call to the control plane per message.** The data API pulls a policy snapshot (applications, permissions, credential hashes) every few seconds and works from memory. If the control plane is down, ingestion, queries and live data keep working with the last snapshot.
- **EMQX** carries events and commands between data API replicas, and serves clients that choose to speak MQTT. Applications using the SDK do not connect to it.
- **ClickHouse** stores the data: telemetry, events and inference results. It is a column store built for this shape of data: append only, read by time range, aggregated. The data API creates its database and table at start.
- **PostgreSQL** keeps what changes after it is written: commands and their state, and dead letters, in a `dataplane` schema with its own migrations. The control plane keeps using PostgreSQL for everything it owns.
- Storage sits behind an interface. A small installation can keep everything in PostgreSQL with `DATAPLANE_STORAGE=postgres` and run no ClickHouse.

Inside the binary, each concern is a package behind an interface: `identity` (policy), `envelope` (validation), `ingest` (bounded queue, batching, retries), `storage`, `bus` (EMQX or in-memory), `live` (subscriptions), `commands`, `api` (HTTP and WebSocket), `mqttgw` (MQTT ingestion).

### Design decisions

These differ from the first plan. Each was chosen to keep the system simple to run and simple to use.

| Decision | Why |
| --- | --- |
| The SDK talks HTTPS and WebSocket to one endpoint, not MQTT | One address and one token for edge and cloud, works through proxies and firewalls, and a publish gets a real answer ("stored" or an error). MQTT cannot tell a publisher that a message was rejected |
| One binary instead of separate ingest, query and command services | The roles are separate packages behind interfaces and can be split when load requires it. Three deployments would add operations work today for no gain |
| ClickHouse for data, PostgreSQL for commands | Measurements are written once and read by time range, which is what ClickHouse is fast and cheap at (it compresses them to a fraction of the size). A command changes state several times and needs row locks, which is what PostgreSQL is good at. Each store does the job it fits |
| ClickHouse is reached over its HTTP interface, with no driver library | No new dependency in the binary. Values travel as typed query parameters, never inside the SQL text |
| Small publishes are grouped by ClickHouse, and still confirmed | ClickHouse dislikes many tiny inserts. The data API uses its asynchronous inserts and waits for the flush, so a publish still answers only when the data is stored |
| No Kafka | The ingestion queue is bounded and in process, with backpressure to the caller, and EMQX shared subscriptions split MQTT load between replicas. Kafka becomes worthwhile for replaying streams into several independent consumers or ingestion that must outlive long storage outages. Neither is needed yet |
| Direct MQTT uses a short-lived signed token with the topic list inside | EMQX 5.8 allows one HTTP authorization source, which the device fleet uses. A signed token needs no call from the broker to the data API and expires on its own |
| The Go service lives beside the Python data plane | `src/data-plane/data_plane` keeps serving device management topics (`devices/...`). The Go service owns `meteor/v1/...`. Nothing was migrated or broken |

## Guarantees in short

- A publish answers success only after the message is durably stored. Overload and storage failures are reported, never hidden.
- Publishing the same message id twice stores it once.
- Late and out of order data is accepted (30 days back, 5 minutes ahead).
- Identity comes from the token. An organization, device or application named in a request body is ignored.
- One organization can never read, subscribe to or command another organization's data.
- Commands are delivered at least once and tracked to a final state.

Details: [Delivery semantics](../../contracts/data-plane-v1.md#delivery-semantics).
