# Operating the data plane

The data API is one stateless Go binary (`meteorcloud/data-api` image, source `src/data-plane/go`). It needs ClickHouse for the data, PostgreSQL for commands, the control plane's policy endpoint and, for more than one replica, an MQTT broker (direct MQTT clients need EMQX).

## Run

| Where | How |
| --- | --- |
| Laptop | `make dev` with `data-api` and `clickhouse` in `COMPOSE_PROFILES` (the default in `.env.example`). API on `http://localhost:8082` |
| Production Compose | Add `data-api` and `clickhouse` to `COMPOSE_PROFILES`, set the secrets below, including `CLICKHOUSE_PASSWORD`. Traefik routes `https://<domain>/v1/...` to it, so `DATA_PLANE_PUBLIC_URL=https://<domain>` |
| Standalone | Run the binary anywhere it can reach ClickHouse, PostgreSQL and the control plane |

Not wired yet: the Ansible installer (`make up`, `make aws-up`) and the Helm chart do not deploy the data API. On an installer-managed server, add the profile and the settings below to the environment file by hand. On Kubernetes, run the image as a Deployment with the settings below.

Two listeners:

| Port | For | Expose |
| --- | --- | --- |
| `8082` (`DATAPLANE_HTTP_ADDR`) | Applications: `/v1/*`, `/healthz` | Publicly, behind TLS |
| `8092` (`DATAPLANE_OPS_ADDR`) | `/metrics`, `/readyz`, `/internal/v1/*` (control plane) | Never. Internal network only |

## Settings

Shared with the control plane:

| Variable | Where | Meaning |
| --- | --- | --- |
| `DATA_PLANE_INTERNAL_TOKEN` | both | Shared secret for the policy feed and operator commands. **No default: while it is empty the control plane refuses every policy request and the data API does not start** |
| `DATA_PLANE_API_URL` | control plane | The data API's ops listener, e.g. `http://data-api:8092`. Empty disables operator commands |
| `DATA_PLANE_PUBLIC_URL` | control plane | Address handed to applications as `METEORCLOUD_ENDPOINT`. Devices must be able to reach it |
| `APP_CREDENTIAL_TTL_HOURS` | control plane | Lifetime of tokens issued to deployed workloads (default 168) |

Data API. Any secret can instead be read from a file by setting `<NAME>_FILE`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONTROL_PLANE_URL` | required | Where to pull the policy from |
| `DATAPLANE_STORAGE` | `postgres` (binary), `clickhouse` (Compose) | `clickhouse`: data in ClickHouse, commands in PostgreSQL. `postgres`: everything in PostgreSQL. `memory` for development: data is lost on restart |
| `DATAPLANE_CLICKHOUSE_URL` | required with `clickhouse` | HTTP address of ClickHouse, for example `http://clickhouse:8123`. Use `https://` when it is not on a private network |
| `DATAPLANE_CLICKHOUSE_DATABASE`, `DATAPLANE_CLICKHOUSE_USER`, `DATAPLANE_CLICKHOUSE_PASSWORD` | `meteorcloud`, `default`, empty | Login and database. The database and the `events` table are created at start. The password can come from a file with `DATAPLANE_CLICKHOUSE_PASSWORD_FILE` |
| `DATAPLANE_CLICKHOUSE_ASYNC_INSERT` | `true` | Let ClickHouse group small inserts. A publish is still confirmed only after the flush |
| `DATAPLANE_DATABASE_URL` | required | PostgreSQL URL for commands and dead letters. Tables live in schema `dataplane`; migrations run at start |
| `DATAPLANE_BUS` | `mqtt` | `memory` for a single replica without a broker |
| `DATAPLANE_MQTT_URL` | required with `mqtt` | e.g. `mqtts://mqtt-broker:8883` |
| `MQTT_PLATFORM_USERNAME`, `MQTT_PLATFORM_PASSWORD`, `MQTT_CA_CERT_PATH` | | The platform's broker login, same as the control plane |
| `DATAPLANE_MQTT_JWT_SECRET` | empty | 32+ characters. Enables `POST /v1/mqtt/credentials`. Direct MQTT works only with `MQTT_PROVIDER=emqx`: EMQX must verify with the same value (`EMQX_AUTHENTICATION__2__ENABLE=true`, `EMQX_AUTHENTICATION__2__SECRET`). In Compose the broker refuses to start when `DATAPLANE_MQTT_ENABLED=true` and this is missing |
| `DATAPLANE_MQTT_PUBLIC_URL` | | Broker address given to MQTT clients |
| `DATAPLANE_MQTT_TOKEN_TTL` | `1h` | Lifetime of an MQTT login |
| `DATAPLANE_POLICY_INTERVAL` | `5s` | How often the policy is pulled. Upper bound for a revocation to take effect |
| `DATAPLANE_POLICY_MAX_STALE` | `24h` | Refuse all credentials when the control plane has been unreachable this long |
| `DATAPLANE_MAX_PAYLOAD_BYTES` | `65536` | Largest `data` of one message |
| `DATAPLANE_MAX_BATCH` | `500` | Messages per publish request |
| `DATAPLANE_QUEUE_SIZE` | `10000` | Bounded ingestion queue. Full means `503 overloaded` |
| `DATAPLANE_INGEST_WORKERS` | `4` | Parallel batch writers |
| `DATAPLANE_RATE_LIMIT_PER_SECOND`, `DATAPLANE_RATE_LIMIT_BURST` | `200`, `1000` | Per credential, counted in messages |
| `DATAPLANE_QUERY_MAX_LIMIT`, `DATAPLANE_QUERY_MAX_BUCKETS`, `DATAPLANE_QUERY_TIMEOUT` | `1000`, `5000`, `10s` | Bounds for history queries |
| `DATAPLANE_MAX_EVENT_AGE`, `DATAPLANE_MAX_FUTURE_SKEW` | `720h`, `5m` | Accepted window for a message's `time` |
| `DATAPLANE_RETENTION_DAYS` | `0` | Delete events older than this. 0 keeps everything. With ClickHouse it becomes the table's TTL and ClickHouse removes old data itself |
| `DATAPLANE_COMMAND_ACK_TIMEOUT` | `5s` | First wait before a command is redelivered |
| `DATAPLANE_LIFECYCLE_EVENTS` | `true` | Publish `app.connected` / `app.disconnected` on `meteor.lifecycle` |
| `DATAPLANE_WS_ALLOWED_ORIGINS` | none | Browser origins allowed to open the WebSocket. Empty allows same-origin and non-browser clients only |
| `DATAPLANE_SHUTDOWN_TIMEOUT` | `20s` | Time to drain on SIGTERM |
| `LOG_FORMAT`, `LOG_LEVEL` | `json`, `info` | |

## Health and metrics

- `GET :8082/healthz` the process is up.
- `GET :8092/readyz` 200 only when the policy is loaded and fresh, storage answers and the transport is connected. The body names the failing check. This is the container health check (`dataplane healthcheck`).
- `GET :8092/metrics` Prometheus, prefix `meteorcloud_dataplane_`.

| Metric | Watch for |
| --- | --- |
| `messages_received_total{source}`, `messages_processed_total`, `messages_rejected_total{source,reason}` | Throughput; a rising `reason="overloaded"` or `forbidden_stream` |
| `ingest_queue_depth` / `ingest_queue_capacity` | Sustained above 80 %: storage is the bottleneck |
| `ingest_latency_seconds`, `storage_write_seconds` | Time from receipt to durable storage |
| `storage_errors_total{op}` | Any increase |
| `dead_letters_total{reason}` | MQTT messages that could not be accepted. Inspect `dataplane.dead_letters` |
| `live_connections`, `live_subscriptions`, `live_delivered_total`, `live_dropped_connections_total{reason}` | `slow_consumer` drops mean a subscriber cannot keep up |
| `failed_deliveries_total`, `bus_connected`, `bus_reconnects_total` | Transport health |
| `commands_total{state}`, `command_redeliveries_total`, `command_dispatcher_lag_seconds` | Commands timing out or being redelivered often: the target is offline or slow |
| `policy_last_success_timestamp_seconds`, `policy_syncs_total{result}`, `policy_credentials` | Policy older than a minute: the control plane is unreachable |
| `auth_failures_total`, `rate_limited_total` | Bad tokens, clients exceeding their rate |
| `http_requests_total{route,method,status}`, `http_request_duration_seconds` | API health |

Suggested alerts: `bus_connected == 0` for 2 minutes; `time() - policy_last_success_timestamp_seconds > 300`; `ingest_queue_depth / ingest_queue_capacity > 0.8` for 5 minutes; any increase of `storage_errors_total` or `dead_letters_total{reason="storage_unavailable"}`.

Logs are JSON lines on stdout. Tokens, credentials and message payloads are never logged; identifiers (organization, application, stream, command id) are.

## Security

- **Identity from the token only.** Organization, application, device and environment are taken from the credential. Request bodies and MQTT payloads cannot override them.
- **Least privilege.** An application can do nothing until permissions are granted, and patterns cannot grant everything (`**` alone is refused).
- **Tenant isolation.** Every storage query and every live match is bound to the caller's organization. Command targets are resolved inside the sender's organization.
- **Tokens.** Opaque, 256 bits of randomness, only the SHA-256 hash is stored. They expire, can be rotated and revoked, and revocation reaches open connections within the policy interval. Direct MQTT is the exception: a connected MQTT client keeps its session until its broker token expires (`DATAPLANE_MQTT_TOKEN_TTL`); see the known limits in the [contract](../../contracts/data-plane-v1.md#mqtt).
- **Fail closed.** No policy, or a policy older than `DATAPLANE_POLICY_MAX_STALE`, means every request is refused.
- **TLS.** Terminate TLS in front of port 8082 (Traefik or your ingress). The broker connection is TLS with the platform CA. Use `https://` for `DATA_PLANE_PUBLIC_URL` in production: the token travels in a header.
- **Secrets.** `DATA_PLANE_INTERNAL_TOKEN`, `DATAPLANE_MQTT_JWT_SECRET`, the database URL and the broker password come from the environment or `_FILE` variants, never from images or committed files. The values in `.env.example` are for local development only.
- **MQTT.** Application logins are short-lived signed tokens with their topic list inside. No application wildcard can cross an organization, commands are never retained, and a client that steps outside its topics is disconnected.
- **Limits.** Payload size, batch size, per-credential rate, query rows, buckets and time are all bounded.
- **On devices.** The token file sits in a root-only directory and is mounted read-only into the one container that is bound to the application.

## Scaling

- Run several replicas behind a load balancer with `DATAPLANE_BUS=mqtt`. Replicas share nothing but ClickHouse, PostgreSQL and the broker. WebSocket connections can land on any replica.
- The command sweeper claims work with row locks, so replicas do not deliver the same redelivery twice.
- MQTT ingestion uses a shared subscription, so the broker splits publishes between replicas.
- ClickHouse takes the write load. It needs about 1 GB of memory even when idle; on a very small server use `DATAPLANE_STORAGE=postgres` and leave the `clickhouse` profile off. Moving from PostgreSQL to ClickHouse later does not copy old events: they stay in `dataplane.events` in PostgreSQL.
- An id is checked before it is stored, so a repeated publish stores nothing twice. Two replicas that receive the same id in the same instant can both store it.
- First limits you will meet: one ClickHouse server (add replicas or use a managed ClickHouse through `DATAPLANE_CLICKHOUSE_URL`) and the bundled broker being a single node (see [licensing](../licensing.md)).
- Kafka is not part of the design. Add it when several independent consumers must replay the same stream, or when ingestion must outlive long storage outages. The ingestion pipeline is the place to put it.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Data API exits at start with `DATA_PLANE_INTERNAL_TOKEN is required` | Set the same value on the control plane and the data API |
| `/readyz` says `policy: ...` | The control plane is unreachable or the internal token differs. `curl -H "X-Data-Plane-Internal-Token: ..." $CONTROL_PLANE_URL/internal/data-plane/policy` must answer 200 |
| `/readyz` says `transport: not connected` | Broker address, CA or platform password. The log shows each attempt |
| SDK: `AuthenticationError invalid_credentials` | Token expired, revoked, application disabled, or wrong endpoint. A token created seconds ago needs one policy poll; the SDK retries that by itself |
| SDK: `PublishError forbidden_stream` | The application's `publish` permission does not cover the stream. `client.whoami().permissions` shows what the token may do |
| SDK: `ConfigurationError` about the token file | The container is not bound to an application, or runs outside MeteorCloud without `METEORCLOUD_TOKEN_FILE` |
| Workload failed with "no data plane address" | Set `DATA_PLANE_PUBLIC_URL` on the control plane, or `METEORCLOUD_ENDPOINT` in the release env |
| Workload failed with "Could not obtain application credentials" | The application named in the release does not exist or is disabled |
| `503 overloaded` | Ingestion queue full. Check storage latency; the SDK retries |
| Subscriber misses events | It was disconnected (`slow_consumer` or network). Live delivery is not replayed: read the gap with `query` |
| Command stays `DISPATCHED`, then `TIMEOUT` | The target application is not connected, has no handler running, or lacks `receive_commands` |
| MQTT client is disconnected right after subscribing | The topic is outside the login's `subscribe` list. EMQX disconnects on a denied action |
| MQTT publishes do not show up in history | Look in `dataplane.dead_letters`: the reason column says why |
