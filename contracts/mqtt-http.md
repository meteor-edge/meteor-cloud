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
