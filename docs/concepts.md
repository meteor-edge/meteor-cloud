# Concepts

Short model of how MeteorCloud is put together. Deeper wiring: [Architecture](architecture.md).

## Three planes

| Plane | Runs where | Job |
| --- | --- | --- |
| **Device plane** | On each Linux device (`meteorcli`) | Enroll, heartbeat, inventory, MQTT client |
| **Data plane** | In your cloud deployment | Platform MQTT session to EMQX; forward inbound messages to the control plane |
| **Control plane** | In your cloud deployment | Identity, organizations, device registry, artifacts, MQTT policy, operator API, PostgreSQL |

The **console** is the operator UI. The browser talks only to the control-plane API.

```text
Device (meteorcli)
    │  HTTPS enroll / heartbeat
    ├──────────────────────────────────► Control plane ──► PostgreSQL
    │                                         ▲
    │  MQTT status / metrics                  │ HTTP ingest
    └──────────────► EMQX ──► Data plane ─────┘
                           ◄── commands (via data plane publish)
```

## Organizations

Everything fleet-related is scoped to an **organization** (tenant). Users join via **memberships** with a role. Optional **access bindings** limit a member to specific device groups.

See [Identity and organizations](identity-and-organizations.md) and [RBAC](authorization/rbac.md).

## Devices

| Concept | Meaning |
| --- | --- |
| **Device type** | Hardware / software catalog entry (e.g. “Raspberry Pi 4”) |
| **Device group** | Logical set (e.g. “production”) — a device belongs to at most one |
| **Device** | One enrolled machine; identity is its UUID |
| **Registration token** | One-time (or limited-use) secret an admin issues for `meteorcli register` |
| **Enrollment API key** | Org-scoped key so a device can `request-token` without a pre-created token |

Connectivity for operators is mainly `last_seen_at` from heartbeats; MQTT online/offline is separate when MQTT is enabled.

## Artifacts

Deployable files (OS image, firmware, compose bundle, …). **Metadata** lives in PostgreSQL; **bytes** live in filesystem or S3-compatible storage. See [Artifacts](fleet/artifacts.md).

## MQTT (optional)

Off by default. When enabled, devices get per-device MQTT credentials at enroll/claim time, connect over TLS, and publish only to ACL-allowed topics. The data plane ingests those payloads into the control plane. See [MQTT](fleet/mqtt.md).
