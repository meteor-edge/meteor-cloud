# Data plane protocol v1

The contract between applications (and SDKs) and the MeteorCloud application data plane (`src/data-plane/go`), and between the data plane and the control plane. The Python SDK (`sdk/python`) is one client of this contract. Machine-readable HTTP description: [`src/data-plane/go/api/openapi.yaml`](../src/data-plane/go/api/openapi.yaml).

Version 1 is identified by the `/v1` path prefix, the `meteor/v1/` topic prefix and `schema_version: 1` in the envelope. Additions (new optional fields, new endpoints) stay in v1. A change that would break an existing client gets a new version next to this one.

## Authentication

Every request carries the application token: `Authorization: Bearer mca_...`. The token is opaque. The data plane knows only its SHA-256 hash, which it receives from the control plane in the policy snapshot.

The token decides the identity of the caller. Nothing in a request body, query string or topic payload can change it:

| Field | Source |
| --- | --- |
| `organization_id`, `application_id`, `application` (slug) | the application the credential belongs to |
| `device_id` | the device the credential was issued for, or absent for a cloud credential |
| `environment` | set on the credential |
| permissions | the application's current permissions |

A revoked, expired or disabled credential stops working within one policy poll (5 seconds by default), including on open WebSocket connections.

## Streams and permissions

A stream name is 1 to 8 segments joined by dots, at most 128 characters in total. A segment is `[a-z0-9]` followed by up to 62 of `[a-z0-9_-]`. Names starting with `meteor.` are reserved for the platform.

A permission pattern is a stream name in which a segment may be `*` (exactly one segment) and the last segment may be `**` (one or more segments). A bare `**` is not allowed.

| Permission | Grants |
| --- | --- |
| `publish: [patterns]` | publish to matching streams |
| `subscribe: [patterns]` | receive matching streams live. A subscription must be covered by one pattern |
| `read: [patterns]` | query history of matching streams |
| `receive_commands: bool` | be the target of commands |
| `send_commands: [application slugs]` | send commands to those applications in the same organization |

## Envelope

Every stored message has this shape. It is what queries and subscriptions return.

```json
{
  "id": "0193a1c2-...",
  "schema_version": 1,
  "kind": "telemetry",
  "type": "energy.metrics",
  "stream": "energy.metrics",
  "organization_id": "5fc1...",
  "application_id": "9a0e...",
  "application": "meter",
  "device_id": "f706...",
  "environment": "production",
  "time": "2026-11-11T10:00:00Z",
  "received_at": "2026-11-11T10:00:00.412Z",
  "seq": 42,
  "data": {"power": 1200, "voltage": 230}
}
```

| Field | Rule |
| --- | --- |
| `id` | Client supplied (1 to 64 of `A-Z a-z 0-9 . _ : -`) or generated (UUIDv7). Unique per organization: the idempotency key |
| `kind` | `telemetry`, `event` or `inference`. `lifecycle` is produced by the platform only |
| `type` | Free label up to 128 characters. Defaults to the stream name |
| `time` | When it happened. Defaults to `received_at`. Accepted from 30 days in the past to 5 minutes in the future |
| `received_at` | Set by the data plane |
| `seq` | Optional publisher sequence number, stored as given |
| `device_id`, `environment` | Omitted when empty |
| `data` | A JSON object, at most 64 KB |

Ordering: events are ordered by `time`, then `id`. The data plane does not reorder or reject late data inside the accepted window.

## Delivery semantics

- **Accepted means persisted.** `POST /v1/messages` answers 200 only after every message marked `stored` or `duplicate` is durably written. There is no "accepted but not yet stored" state over HTTP.
- **At least once, deduplicated.** A client that gets no answer retries with the same ids. The first write of an `(organization, id)` wins. A retry is reported as `duplicate` and stores nothing.
- **Backpressure, not loss.** A full ingestion queue answers `503 overloaded` with `Retry-After`. A storage failure answers `503 storage_unavailable`. In both cases nothing from the request may be assumed stored.
- **Live delivery follows storage** and is best effort: a subscriber gets an event after it was stored, at most once per connection. A subscriber that cannot keep up is disconnected with close code 4008 and can backfill from history.
- **MQTT publishers have no reply channel.** A message published over MQTT that cannot be accepted is written to the dead-letter table with a reason. The broker is acknowledged (QoS 1) only after the message is stored or dead-lettered.
- **Commands are at least once.** A command is redelivered with exponential backoff until the target acknowledges `received` or the command expires. Handlers must tolerate repeats.

## HTTP API

Base: the public listener (`:8082`). All bodies are JSON. Errors:

```json
{"error": {"code": "forbidden_stream", "message": "This application may not read stream \"billing.x\"."}}
```

| Status | Codes |
| --- | --- |
| 400 | `invalid_json`, `no_messages`, `invalid_stream`, `invalid_parameter`, `too_many_buckets`, `invalid_command` |
| 401 | `missing_credentials`, `invalid_credentials` |
| 403 | `forbidden_stream`, `forbidden_command` |
| 404 | `command_not_found`, `mqtt_not_enabled` |
| 413 | `batch_too_large`, `body_too_large` |
| 429 | `rate_limited` (per credential; `Retry-After`) |
| 503 | `overloaded`, `storage_unavailable`, `policy_unavailable`, `shutting_down` (`Retry-After`) |
| 504 | `query_timeout` |

### `GET /v1/whoami`

The identity and permissions of the token: `organization_id`, `application_id`, `application`, `environment`, `device_id` (null for cloud), `credential_id`, `expires_at`, `permissions`.

### `POST /v1/messages`

```json
{"messages": [
  {"id": "optional", "kind": "telemetry", "type": "optional", "stream": "energy.metrics",
   "time": "2026-11-11T10:00:00Z", "seq": 42, "data": {"power": 1200}}
]}
```

Up to 500 messages. Only `stream` and `data` are required. Response 200:

```json
{"results": [{"index": 0, "id": "0193...", "status": "stored"},
             {"index": 1, "id": "abc", "status": "rejected",
              "error": {"code": "forbidden_stream", "message": "..."}}],
 "accepted": 1, "rejected": 1}
```

`status` is `stored`, `duplicate` or `rejected`. Rejection codes: `invalid_stream`, `invalid_kind`, `invalid_id`, `invalid_payload`, `payload_too_large`, `invalid_timestamp`, `forbidden_stream`. One rejected message does not fail the others.

### `GET /v1/streams/{stream}/data`

Needs `read` on the stream. Results are always limited to the caller's organization.

| Parameter | Meaning |
| --- | --- |
| `start`, `end` | RFC 3339. `start` inclusive, `end` exclusive |
| `kind` | `telemetry`, `event` or `inference` |
| `device_id`, `application` | Only that publisher |
| `filter` | JSON object; keeps events whose `data` contains it, e.g. `{"severity":"warning"}` |
| `limit` | 1 to 1000, default 100 |
| `order` | `asc` (default) or `desc` |
| `cursor` | `next_cursor` of the previous page |
| `aggregate` | `avg`, `min`, `max`, `sum`, `count`. Switches to the aggregate response |
| `field` | Numeric field of `data` to aggregate; dots for nesting, at most 4 levels |
| `bucket` | Width: `30s`, `5m`, `1h`, `1d`. Default `1m`, minimum `1s` |

Raw response: `{"stream": "...", "events": [envelope...], "next_cursor": "..." | null}`.

Aggregate response: `{"stream", "aggregate": {"function", "field", "bucket_seconds"}, "start", "end", "buckets": [{"start", "value", "count"}]}`. Without `start` the window is the 24 hours before `end` (default now). A window may hold at most 5000 buckets. Every query has a server-side time limit (10 seconds by default).

### Commands

| Endpoint | Who | Does |
| --- | --- | --- |
| `POST /v1/commands` | needs `send_commands` for the target | Body `{application, name, payload?, device_id?, timeout_seconds?, expires_in_seconds?, max_attempts?, idempotency_key?}`. 202 with the command. The same `idempotency_key` returns the existing command |
| `GET /v1/commands/{id}` | issuer or target | The command |
| `GET /v1/commands/pending` | needs `receive_commands` | `{"commands": [...]}` not yet acknowledged; polling fallback for clients without a streaming connection |
| `POST /v1/commands/{id}/ack` | the target only | Body `{status, result?, error?}` with status `received`, `executing`, `succeeded` or `failed` |

Command object: `id, organization_id, application_id, application, device_id?, name, payload, state, issued_by, idempotency_key?, attempts, max_attempts, timeout_seconds, created_at, updated_at, expires_at, dispatched_at?, received_at?, completed_at?, result?, error?`.

State machine:

```
PENDING -> DISPATCHED -> RECEIVED -> EXECUTING -> SUCCEEDED
                                              \-> FAILED
any non-final state -> TIMEOUT
```

- `DISPATCHED` when handed to the transport. Redelivered (5 s, doubling to 60 s) until `received`.
- `TIMEOUT` when `expires_at` passes before `received`, when `max_attempts` (0 means unlimited) is used up, or when no result arrives within `timeout_seconds` after `received`.
- Acknowledgements may skip states (`succeeded` straight after delivery is valid). A final state never changes. Defaults: `timeout_seconds` 60, `expires_in_seconds` 3600.
- With `device_id`, only the instance of the application on that device may acknowledge. Without it, any instance may.

### `POST /v1/mqtt/credentials`

Exchanges the token for a short-lived broker login for clients that speak MQTT directly. See [MQTT](#mqtt). Response: `{url, username, password, client_id, expires_at, topic_root, live_root, publish: [filters], subscribe: [filters]}`. Answers 404 `mqtt_not_enabled` when direct MQTT is not configured.

## Streaming: `GET /v1/stream` (WebSocket)

Upgrade with the same `Authorization` header. JSON text frames.

Client to server:

```json
{"op": "subscribe", "id": "s1", "stream": "energy.*", "kind": "telemetry", "device_id": "", "application": ""}
{"op": "unsubscribe", "id": "s1"}
{"op": "ping"}
```

Server to client:

```json
{"type": "ready", "identity": {...}}
{"type": "subscribed", "id": "s1"}
{"type": "unsubscribed", "id": "s1"}
{"type": "event", "subscription": "s1", "event": {envelope}}
{"type": "command", "command": {command}}
{"type": "error", "id": "s1", "code": "forbidden_stream", "message": "..."}
{"type": "pong"}
```

- `ready` is the first frame. Subscriptions are per connection: after a reconnect the client subscribes again.
- `kind`, `device_id` and `application` are optional filters. At most 64 subscriptions per connection.
- A connection whose application has `receive_commands` gets `command` frames: pending ones right after `ready`, then new ones. Acknowledge over HTTP.
- The server sends WebSocket pings every 25 seconds.
- Close codes: 4008 `slow_consumer`, 4401 `credential_revoked`, 1001 server shutting down (reconnect).
- When `DATAPLANE_LIFECYCLE_EVENTS` is on, `app.connected` and `app.disconnected` events are published on stream `meteor.lifecycle` (kind `lifecycle`), visible to applications granted `subscribe` on it.

## MQTT

The SDK does not use MQTT. It is offered for clients that already speak it. MQTT 5, TLS, QoS 1.

Login: `POST /v1/mqtt/credentials`, then connect with the returned `username` (`app_<credential id>`), `password` (a JWT, valid one hour by default) and `client_id`. The broker verifies the signature and enforces the topic list embedded in the token, so a client can reach nothing beyond the answer's `publish` and `subscribe` filters. A subscribe or publish outside them disconnects the client.

Topics (a stream's dots become topic levels; `*` becomes `+`, a trailing `**` becomes `+/#`):

| Topic | Direction | Payload |
| --- | --- | --- |
| `meteor/v1/orgs/{org}/apps/{app}/devices/{device or "cloud"}/telemetry/{stream levels}` | client publishes | `{"id"?, "type"?, "time"?, "seq"?, "data": {...}}` |
| `.../events/{stream levels}`, `.../inference/{stream levels}` | client publishes | same |
| `meteor/v1/orgs/{org}/live/{stream levels}` | client subscribes | envelope |
| `meteor/v1/orgs/{org}/apps/{app}/commands` and `.../devices/{device}/commands` | client subscribes | command |
| `meteor/v1/orgs/{org}/apps/{app}/devices/{device or "cloud"}/commands/ack` | client publishes | `{"command_id", "status", "result"?, "error"?}` |

Rules: organization, application and device come from the topic, which the broker restricts to the caller's own. No wildcard subscription reaches another organization or a stream outside the `subscribe` permission. Nothing is published retained: a command is never a retained message. The data plane itself consumes publishes through a shared subscription (`$share/meteor-dataplane/...`), so several replicas split the load.

## Control plane integration

The data plane never calls the control plane while handling a message. It needs it for two things:

**Policy snapshot** (data plane pulls): `GET {CONTROL_PLANE_URL}/internal/data-plane/policy` with header `X-Data-Plane-Internal-Token`. Supports `If-None-Match` and answers 304.

```json
{"version": "33ecd1f4...", "generated_at": "...",
 "applications": [{"id", "organization_id", "slug", "permissions": {...}}],
 "credentials": [{"id", "organization_id", "application_id", "device_id" | null, "environment", "token_hash", "expires_at"}]}
```

Only enabled applications and usable credentials (not revoked, not expired, device not disabled) are listed. The data plane polls every 5 seconds, serves from the last snapshot while the control plane is unreachable, and refuses all credentials once the snapshot is older than `DATAPLANE_POLICY_MAX_STALE` (24 hours).

**Operator commands** (control plane pushes, ops listener `:8092`, same shared token header): `POST /internal/v1/commands` with `{organization_id, application_id, issued_by, name, payload, device_id?, ...}` and `GET /internal/v1/organizations/{org}/commands/{id}`.

**Agent credential minting** (device agent to control plane): `POST /api/v1/agent/applications/credentials` with the device token and `{"workload_key": "..."}`. Answers a device-scoped token only when the device's desired release for that workload is bound to an application: `{token, credential_id, expires_at, refresh_after, endpoint, application, application_id, organization_id, device_id, environment}`.
