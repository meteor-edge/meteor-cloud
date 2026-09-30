---
title: Introduction
description: What MeteorCloud is and how the modules fit together.
section: Overview
order: 1
---

MeteorCloud is a self-hosted platform for managing Linux devices at the edge. You run the servers. Devices run `meteorcli`. Operators use the console in a browser.

The product is split into **modules**. Each module is its own process and can be started from its own Compose file.

| Module | What it does |
| --- | --- |
| **Control plane** | Users, organizations, device registry, enrollment, MQTT policy, last-value telemetry, operator API |
| **Data plane** | Holds the platform MQTT session to EMQX. Publishes commands and forwards inbound messages to the control plane |
| **Console** | Operator UI. The browser talks only to the control plane |
| **Website** | This site: marketing pages and documentation. Independent of the control plane |
| **Device agent** | `meteorcli` on the device: enroll, heartbeat over HTTPS, MQTT over TLS |

Telemetry today is the **latest** status and metrics stored on the device row in PostgreSQL. Timescale and ClickHouse are reserved configuration names, not implemented.

OTA (firmware rollout) is a control-plane concern behind an `OTAProvider` port. The only implemented value is `none`. A later vendor (for example Mender) plugs in without rewriting MQTT.

Continue with [Architecture](/docs/architecture) or [Get started](/docs/getting-started).
