---
title: Architecture
description: How control plane, data plane, console, and devices communicate.
section: Overview
order: 2
---

Application modules do not import each other’s code. They use HTTP, MQTT, and environment variables.

```
Browser ──► Console ──► Control plane ──► PostgreSQL / Redis
meteor-agent HTTPS ──► Control plane
meteor-agent MQTT ──► EMQX
EMQX HTTP auth ──► Control plane
Control plane HTTP ──► Data plane (publish / watch)
Data plane HTTP ──► Control plane (ingest)
```

The **website** is not on this path. It does not log into the API.

## Deploy separately

On one machine, Compose projects share the Docker network `meteorcloud` and use service names (`backend`, `data-plane`).

On different servers, set:

- Control plane: `DATA_PLANE_URL=http://<data-plane-host>:8081`
- Data plane: `CONTROL_PLANE_URL=http://<control-plane-host>:8000`
- Console: `VITE_API_BASE_URL` to the public control-plane origin
- EMQX auth URLs in `emqx.conf` (default `http://backend:8000/...`) to the control plane
- Shared secret: `MQTT_INTERNAL_TOKEN`

Keep `/internal/mqtt/*` and data-plane `/v1/*` off the public internet.

Scale the control plane and console freely. Run **one** data-plane MQTT subscriber until shared subscriptions exist.

## Providers

| Setting | Implemented | Reserved |
| --- | --- | --- |
| `DATABASE_PROVIDER` | `postgresql` | — |
| `CACHE_PROVIDER` | `redis` | — |
| `MQTT_PROVIDER` | `emqx` | other names fail fast |
| `TELEMETRY_PROVIDER` | `postgresql` | `timescale`, `clickhouse` |
| `OTA_PROVIDER` | `none` | `mender` later |
