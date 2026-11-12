# Third-party component licensing

Guidance for images and tools MeteorCloud can start. **Not legal advice.**

**Product model:** customers **self-host** MeteorCloud, or MeteorEdge **manages it on their infrastructure**. Prefer **permissive** licenses (Apache 2.0, MIT, BSD, PostgreSQL) for anything that is a **production default**. Application code (control plane, data plane, console, `meteorcli`) is separate from these vendor processes.

## Production OK (read this first)

| Component | Role | License | Production OK? | Meaning |
| --- | --- | --- | --- | --- |
| PostgreSQL | Database | [PostgreSQL](https://www.postgresql.org/about/licence/) | **Yes** | Fine to ship as default |
| Traefik (open-source Proxy) | HTTPS proxy | [MIT](https://github.com/traefik/traefik/blob/master/LICENSE.md) | **Yes** | Fine to ship as default |
| Redis **7.2** (pinned) | Optional cache / rate limits | [BSD-3-Clause](https://redis.io/legal/licenses/) (≤7.2) | **Yes** | OK when enabled. Pin `redis:7.2-alpine`. Do **not** use floating `redis:7` / `7-alpine` (tracks 7.4+ RSALv2/SSPLv1) |
| EMQX **5.8.6** (single node) | Optional MQTT | Apache 2.0 | **Yes** | OK as shipped. Details: [MQTT → EMQX licensing](fleet/mqtt.md#emqx-version-and-licensing) |
| ClickHouse **25.8** (`clickhouse/clickhouse-server:25.8-alpine`) | Optional store for application data (profile `clickhouse`) | [Apache 2.0](https://github.com/ClickHouse/ClickHouse/blob/master/LICENSE) | **Yes** | Unmodified image, reached over its HTTP interface; no ClickHouse library is linked into MeteorCloud. ClickHouse Cloud and its enterprise features are separate products and are not used |
| Filesystem or customer S3 / R2 / GCS | Artifacts | N/A / cloud contract | **Yes** | **Preferred** production storage |
| WireGuard / Docker | VPN / runtime | GPL family / Apache 2.0 | **Yes** | Normal host infra |
| Prometheus / Alertmanager / node_exporter | Metrics / alerts | Apache 2.0 | **Yes** | Optional ops stack; fine |
| kube-prometheus-stack **93.1.1** (Prometheus Operator, kube-state-metrics) | Local k3d monitoring (`make k8s-deploy OBS=1`) | [Apache 2.0](https://github.com/prometheus-community/helm-charts) | **Yes** | Local k3d only, off by default. Its bundled Grafana follows the Grafana row. Production clusters bring their own Prometheus |
| Grafana / Loki / Alloy | Ops dashboards / logs | AGPLv3 | **Yes, with limits** | Optional **operator** tools on **localhost / SSH** only. Off by default. **Not** a customer-facing MeteorCloud UI feature. See [below](#grafana--loki--alloy-yes-with-limits) |
| Data API Go modules: `pgx` (MIT), `prometheus/client_golang` (Apache 2.0), `google/uuid` and `golang.org/x/time` (BSD-3-Clause), `coder/websocket` (ISC), `eclipse/paho.golang` (EPL-2.0 **or** EDL-1.0, BSD-3-Clause style) | Application data plane (`src/data-plane/go`) | Permissive | **Yes** | Linked into the `data-api` binary. `paho.golang` is used unmodified under its EDL-1.0 option, like the Python `paho-mqtt` the platform already uses |
| Python SDK dependencies: `httpx`, `websockets` | `sdk/python` | BSD-3-Clause | **Yes** | Installed by application developers alongside the SDK |
| `golang:1.24-alpine` (build) / `alpine:3.21` (runtime) images | Data API image | BSD-3-Clause (Go) / MIT and GPL-2.0 userland (Alpine, BusyBox) | **Yes** | Unmodified base image; only the static Go binary is added. Same position as the other Alpine-based images |
| MinIO | S3-compatible artifacts | [AGPLv3](https://github.com/minio/minio) | **No (as default)** | Dev/lab only. Production Ansible/Helm default to **filesystem** or real S3. Do not make MinIO the commercial default |
| `cryptography` (Python, control plane) | Encrypts a Wi-Fi password while it waits for its device (AES-GCM) | [Apache-2.0 OR BSD-3-Clause](https://github.com/pyca/cryptography/blob/main/LICENSE) | **Yes** | Permissive. Already installed through `python-jose[cryptography]`; now also a direct dependency |
| EMQX **5.9+** cluster | MQTT HA | BSL 1.1 | **Only with commercial license** | Multi-node / some commercial cases need an [EMQ deal](https://www.emqx.com/en/contact). Do not bump casually |

**One-line policy**

> Production defaults = only **Yes**.  
> **Yes, with limits** = customer’s (or our) ops team on that install, not end-user product UI.  
> **No (as default)** / **Only with commercial license** = do not enable without an explicit decision.

## Grafana / Loki / Alloy (“Yes, with limits”)

| OK | Not OK (without counsel / Grafana commercial terms) |
| --- | --- |
| Observability flag off by default | Marketing “Observability included” that is really public Grafana |
| Grafana on `127.0.0.1`, access via SSH tunnel | Grafana on the public Traefik hostname for all users |
| Customer SRE or MeteorEdge ops debugging **that** deployment | Multi-tenant SaaS where every customer opens Grafana in the browser as a MeteorCloud feature |
| Unmodified images as sidecars next to the app | Shipping modified Grafana/Loki without AGPL compliance |

AGPL does **not** by itself force MeteorCloud’s Python/React code to become AGPL when these run as separate containers. Risk rises when you **offer Grafana/Loki as a network service to customers**.

## Pins and where they live

| Component | Compose | Helm |
| --- | --- | --- |
| PostgreSQL | `deploy/compose/control-plane.yml` → `postgres:16-alpine` | `values.yaml` → `postgres.bundled.image` |
| Redis | `deploy/compose/control-plane.yml` → `redis:7.2-alpine` | `values.yaml` → `cache.redis.bundled.image` |
| Traefik | `deploy/compose/docker-compose.prod.yml` → `traefik:v3.3` | `values.yaml` → `ingress.traefik.image` |
| EMQX | `deploy/compose/data-plane.yml` → `emqx/emqx:5.8.6` | `values.yaml` → `mqtt.emqx.image` |
| ClickHouse | `deploy/compose/data-plane.yml` → `clickhouse/clickhouse-server:25.8-alpine` | not in the chart |
| Data API base images | `src/data-plane/go/Dockerfile` → `golang:1.24-alpine`, `alpine:3.21` | not in the chart |
| Data API Go modules | `src/data-plane/go/go.mod` | same |
| MinIO (dev) | `deploy/compose/control-plane.yml` (`MINIO_IMAGE`) | not in the chart |
| Observability | `deploy/compose/docker-compose.observability.yml` | not in the chart |

## For coding agents

When adding or upgrading a third-party dependency or image, check this page, state **Production OK**, and surface a **LICENSING RISK** note to the human. Rules: [AGENTS.md](../AGENTS.md), Cursor `.cursor/rules/third-party-licensing.mdc`.

## Upstream references

- [EMQX Licensing FAQ](https://www.emqx.com/en/content/license-faq)
- [Redis licenses](https://redis.io/legal/licenses/)
- [MinIO / AGPL](https://github.com/minio/minio)
- [Traefik LICENSE (MIT)](https://github.com/traefik/traefik/blob/master/LICENSE.md)
- [PostgreSQL licence](https://www.postgresql.org/about/licence/)
