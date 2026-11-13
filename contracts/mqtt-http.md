# MQTT HTTP contract between control-plane and data-plane
#
# Shared secret: header `X-MQTT-Internal-Token` must match `MQTT_INTERNAL_TOKEN`.
# JSON bodies only. No shared Python package.

## Control plane (`CONTROL_PLANE_URL`)

### `POST /internal/mqtt/authenticate`

EMQX (or a future broker adapter) calls this. Body:

```json
{ "username": "device_<uuid>", "password": "mqtt_..." }
```

Response: `{ "result": "allow"|"deny", "is_superuser": false }`

### `POST /internal/mqtt/authorize`

```json
{ "username": "device_<uuid>", "action": "publish"|"subscribe", "topic": "devices/<uuid>/status" }
```

Response: `{ "result": "allow"|"deny" }`

### `POST /internal/mqtt/ingest`

Data-plane calls this for every inbound MQTT payload. Body:

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

## Application logins (`app_` usernames)

Applications that connect to the broker directly use a username starting with `app_` and a short-lived signed token as password ([data-plane-v1.md](data-plane-v1.md#mqtt)). For these usernames the control plane answers `{"result": "ignore"}` to both `authenticate` and `authorize`: EMQX then falls through to its JWT authenticator, which verifies the token and applies the topic list inside it. A device or platform login never starts with `app_`.
