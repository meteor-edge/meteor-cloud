# MeteorCloud

**Self-hosted fleet platform for Linux devices.**

Enroll machines you own, group them, ship software, watch telemetry, and send commands. You run the cloud; device data stays on your infrastructure.

Website: [meteor-edge.com](https://meteor-edge.com)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## What happens

A device runs [`meteorcli`](docs/meteorcli.md). Operators use the console, which talks only to the control-plane API.

```text
meteorcli
  │  HTTPS   enroll, heartbeat, inventory
  ├──────────────────────────────────────────► control plane ──► PostgreSQL
  │                                                    ▲
  │  MQTT    metrics, status, events, commands         │ live view and commands
  └──────────► broker ──► mqtt-forwarder ──────────────┘
                  │
                  └──► device-ingest ──► NATS JetStream
                                            ├─► rule-processor
                                            └─► clickhouse-writer ──► ClickHouse
```

1. **Enroll.** The agent registers over HTTPS, then heartbeats and reports inventory. Organizations, roles, and the audit log live in the control plane.
2. **Talk over MQTT.** The broker (Mosquitto by default) carries device metrics, status, events, and commands. Each device gets its own credentials and an ACL.
3. **Keep the console live.** `mqtt-forwarder` forwards those messages to the control plane and publishes commands back to the device.
4. **Fan out on NATS.** `device-ingest` binds each message to its device and organization and publishes it to **NATS JetStream**. `rule-processor` and `clickhouse-writer` consume that stream on their own: rules and alerts, and history in ClickHouse.
5. **Ship software.** Artifacts (OS images, firmware, compose bundles) keep metadata in PostgreSQL and bytes on a filesystem volume or any S3-compatible store.
6. **Let applications in.** The data API serves publish, subscribe, query, and commands over HTTPS and WebSocket. The [Python SDK](sdk/python/README.md) is the client. Applications do not connect to the broker.

NATS is internal messaging between data-plane services. Devices connect only over HTTPS and MQTT. The console’s live view comes from the MQTT forwarder, so the control plane keeps working when the event path is off.

The event path starts with Compose profile `nats` or Helm `nats.enabled`, and needs the MQTT broker and ClickHouse. Design: [docs/data-plane/jetstream.md](docs/data-plane/jetstream.md).

## Services

| Service | Role |
| --- | --- |
| `meteorcli` | On the device: enroll, heartbeat, MQTT, deploys, local actions |
| Console | Operator UI |
| Control plane | Identity, tenancy, device registry, artifacts, operator API |
| MQTT broker | Device transport. Mosquitto by default; EMQX or VerneMQ if you select them |
| `mqtt-forwarder` | Platform MQTT session: ingest into the control plane, commands back out |
| `device-ingest` | MQTT device topics → NATS JetStream |
| `rule-processor` | Rules and alerts from the JetStream stream |
| `clickhouse-writer` | Batched history into ClickHouse |
| Data API | Application publish, subscribe, query, and commands |
| PostgreSQL | Required. Control-plane data, and data-plane commands |
| ClickHouse | Telemetry and event history |
| Redis | Optional shared rate limits across API replicas |

PostgreSQL is required. MQTT, NATS, ClickHouse, Redis, and object storage are selected in config. The smallest install is PostgreSQL, the API, the console, and a reverse proxy (Traefik with Compose, your ingress with Kubernetes).

## Try it on a laptop

Compose on your machine. This is for development, not production.

```bash
git clone https://github.com/meteor-edge/meteor-cloud.git
cd meteor-cloud
make dev
make seed      # owner@example.com / dev-password-123
```

| | |
| --- | --- |
| Console | http://localhost:5173 |
| API · OpenAPI | http://localhost:8000 · http://localhost:8000/docs |
| MQTT forwarder | http://localhost:8081/health |
| Data API | http://localhost:8082 |
| MQTT | mqtts://localhost:8883 |

`make dev` starts whatever `COMPOSE_PROFILES` lists in `deploy/compose/.env`. The example includes PostgreSQL, Redis, MinIO, Mosquitto, the data API, and ClickHouse. Add `nats` to that list to start NATS, `device-ingest`, `rule-processor`, and `clickhouse-writer`. Stop with `make stop`.

Next: [Getting started](docs/getting-started.md).

## Install

Same images, two paths. MeteorCloud does not create Kubernetes clusters.

| Where | Command | Guide |
| --- | --- | --- |
| VM or existing server | `make up PLATFORM_DOMAIN=fleet.example.com` | [Install quickstart](docs/install-quickstart.md) |
| New AWS EC2 | `make aws-up` from a laptop | [AWS](docs/aws-deployment.md) |
| Cluster you already run | `helm upgrade --install` | [Kubernetes](docs/kubernetes.md) |

`make up` prints DNS records and the first login. Run `make aws-up` from a laptop, not on the VM. Optional WireGuard for device VPN is part of the VM path ([connectivity](docs/fleet/connectivity.md)).

## Repository

```text
src/control-plane/           Python API
src/data-plane/go/           mqtt-forwarder, data API, device-ingest, rule-processor, clickhouse-writer
src/device-plane/agent-go/   meteorcli
console/                     operator UI
deploy/compose/              Docker Compose
deploy/ansible/              VM install
deploy/kubernetes/helm/      Helm chart
contracts/                   HTTP and MQTT contracts
docs/
```

[Documentation index](docs/README.md) · [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md)

## License

Copyright © Meteor Edge contributors. [Apache License 2.0](LICENSE).
