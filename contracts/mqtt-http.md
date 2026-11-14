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

## Application logins (`app_` usernames)

Applications that connect to the broker directly use a username starting with `app_` and a short-lived signed token as password ([data-plane-v1.md](data-plane-v1.md#mqtt)). For these usernames the control plane answers `{"result": "ignore"}` to both `authenticate` and `authorize`: EMQX then falls through to its JWT authenticator, which verifies the token and applies the topic list inside it. A device or platform login never starts with `app_`.
