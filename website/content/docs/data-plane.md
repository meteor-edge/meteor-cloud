---
title: Data plane
description: MQTT gateway process in front of EMQX.
section: Platform
order: 11
---

The data plane (`data-plane/`) is a small FastAPI process. It connects to EMQX as the platform user, subscribes to device topics, and POSTs each inbound payload to the control plane ingest API.

HTTP (header `X-MQTT-Internal-Token`):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness |
| POST | `/v1/publish` | `{ topic, payload, qos, retain }` |
| POST | `/v1/subscriptions` | Extra topic watch (MQTT test UI) |
| POST | `/v1/subscriptions/unwatch` | Drop a watch |

Environment: `CONTROL_PLANE_URL`, `MQTT_BROKER_HOST`, `MQTT_CA_CERT_PATH`, `MQTT_INTERNAL_TOKEN`, `HTTP_ADDR` (default `:8081`).

Start with `compose/data-plane.yml` (EMQX + this process). Auth for devices stays on the control plane; EMQX calls `/internal/mqtt/authenticate` and `/authorize` there.

JSON details: see [API](/docs/api) (internal MQTT section).
