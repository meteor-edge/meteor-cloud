---
title: Control plane
description: Identity, fleet registry, MQTT policy, and the operator API.
section: Platform
order: 10
---

The control plane is a FastAPI application (`control-plane/`). It owns business data.

- Users and JWT login
- Organizations and RBAC (`owner`, `admin`, `member`, `viewer`)
- Device types, groups, devices
- Registration tokens and enrollment API keys
- Device HTTP tokens and MQTT passwords (hashed)
- Last status/metrics on the `Device` row
- Ping **intent** (publish goes to the data plane)
- EMQX authenticate / authorize / ingest HTTP callbacks

It does **not** open a paho MQTT connection. `MQTTGateway` is an HTTP client to `DATA_PLANE_URL`.

Postgres and Redis belong with this module. Start it with `compose/control-plane.yml`.

Configuration that matters: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `DATA_PLANE_URL`, `MQTT_INTERNAL_TOKEN`, `MQTT_PUBLIC_HOST` (what devices should use as the broker hostname).
