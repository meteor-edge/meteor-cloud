# MQTT HTTP contract between control-plane and data-plane
#
# Shared secret: header `X-MQTT-Internal-Token` must match `MQTT_INTERNAL_TOKEN`.
# JSON bodies only. No shared Python package.

## Control plane (`CONTROL_PLANE_URL`)

### `POST /internal/mqtt/authenticate`

The broker calls this (Mosquitto `meteorcloud_auth` plugin, EMQX HTTP authn). Body:

```json
{ "username": "device_<uuid>", "password": "mqtt_...", "clientid": "device-<uuid>" }
```

`clientid` is optional. When present, a device login is denied unless it is
`device-<uuid>` or `device-<uuid>-<suffix>` for the same device.

Response: `{ "result": "allow"|"deny", "is_superuser": false }`

### `POST /internal/mqtt/authorize`

```json
{ "username": "device_<uuid>", "action": "publish"|"subscribe"|"read", "topic": "devices/<uuid>/status",
  "clientid": "device-<uuid>", "retain": false }
```

`clientid` and `retain` are optional (`retain` may be a bool or `"true"`/`"false"`).
`read` is the delivery check Mosquitto makes before handing a message to a subscriber.
Rules: `src/control-plane/app/mqtt/policy.py`. A disabled device or revoked
credential is denied here too.

Response: `{ "result": "allow"|"deny" }`

### `POST /internal/mqtt/vernemq/<hook>?token=<MQTT_INTERNAL_TOKEN>`

VerneMQ `vmq_webhooks` adapter for `auth_on_register`, `auth_on_publish`,
`auth_on_subscribe` and their `_m5` variants. Bodies and answers follow the
VerneMQ webhook format (`{"result":"ok"}`, `{"result":{"error":"not_allowed"}}`,
per-topic `qos: 128` for a denied subscription). Same decisions as above.

### Control topic `meteorcloud/control/disconnect`

Published by the platform user only: `{ "username": "device_<uuid>" }`. The
Mosquitto plugin disconnects that user's sessions. Other brokers ignore it.

### `POST /internal/mqtt/ingest`

Data-plane calls this for every inbound MQTT payload (the MQTT forwarder,
`src/data-plane/go/cmd/mqtt-forwarder`). Delivery is at least once, so a
message may arrive twice after a reconnect:

- 2xx: done.
- Not reachable, 401, 403, 408, 429, 502, 503, 504: retried until it succeeds.
- 500 and other 5xx: retried up to `MQTT_FORWARDER_MAX_ATTEMPTS` (10), then dropped.
- Other 4xx: dropped.

Messages of one device arrive in the order the forwarder received them. That
holds for one forwarder process, which is how it is deployed; with several,
the order across them is not guaranteed. Body:

```json
{ "topic": "devices/<uuid>/status", "payload": "{...}" }
```

Response: `{ "ok": true }`

Control-plane applies last-value status/metrics/command results (`TELEMETRY_PROVIDER=postgresql`).

### `GET /internal/data-plane/devices`

Device ingestion (`device-ingest`) reads which organization owns each enrolled, enabled device. Header `X-Data-Plane-Internal-Token` must match `DATA_PLANE_INTERNAL_TOKEN`. `If-None-Match: "<version>"` returns `304` when nothing changed.

```json
{ "version": "3f2a…", "generated_at": "2026-11-12T12:00:00Z",
  "devices": [ { "id": "<device uuid>", "organization_id": "<org uuid>" } ] }
```

## Data plane (`DATA_PLANE_URL`)

Served on port 8081 by the MQTT forwarder (Go). Errors are `{"detail": "..."}`:
401 for a wrong `X-MQTT-Internal-Token`, 422 for a malformed body, 503 when the
broker session is not up (publish waits up to 15 s for it). Success is `{"ok": true}`.

### `GET /health`

`{ "status": "ok" }`

### `POST /v1/publish`

```json
{ "topic": "devices/<uuid>/commands", "payload": "{...}", "qos": 1, "retain": false }
```

### `POST /v1/subscriptions`

```json
{ "topic": "devices/+/events" }
```

### `POST /v1/subscriptions/unwatch`

```json
{ "topic": "devices/+/events" }
```
