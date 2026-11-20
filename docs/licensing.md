# Third-party component licensing

Guidance for images and tools MeteorCloud can start. **Not legal advice.**

**Product model:** customers **self-host** MeteorCloud, or MeteorEdge **manages it on their infrastructure**. Prefer **permissive** licenses (Apache 2.0, MIT, BSD, PostgreSQL) for anything that is a **production default**. Application code (control plane, data plane, console, `meteorcli`) is separate from these vendor processes.

## Production OK (read this first)

| Component | Role | License | Production OK? | Meaning |
| --- | --- | --- | --- | --- |
| PostgreSQL | Database | [PostgreSQL](https://www.postgresql.org/about/licence/) | **Yes** | Fine to ship as default |
| Traefik (open-source Proxy) | HTTPS proxy | [MIT](https://github.com/traefik/traefik/blob/master/LICENSE.md) | **Yes** | Fine to ship as default |
| Redis **7.2** (pinned) | Optional cache / rate limits | [BSD-3-Clause](https://redis.io/legal/licenses/) (≤7.2) | **Yes** | OK when enabled. Pin `redis:7.2-alpine`. Do **not** use floating `redis:7` / `7-alpine` (tracks 7.4+ RSALv2/SSPLv1) |
| Eclipse Mosquitto **2.x** (Debian package, in `meteorcloud/mosquitto`) | MQTT broker (**default**) | [EPL-2.0 OR EDL-1.0 (BSD-3-Clause)](https://github.com/eclipse-mosquitto/mosquitto/blob/master/LICENSE.txt) | **Yes** | Fine to ship as default. Unmodified; our auth plugin is separate MeteorCloud code. Single node only |
| EMQX **5.8.6** (single node) | Optional MQTT broker (`MQTT_PROVIDER=emqx`) | Apache 2.0 | **Yes** | OK as shipped, but **no upstream security fixes since 2026-02-28**. Details: [MQTT brokers](fleet/mqtt-brokers.md#emqx) |
| VerneMQ (official `vernemq/vernemq` image) | Optional MQTT broker (`MQTT_PROVIDER=vernemq`) | Source: Apache 2.0. Official builds: [VerneMQ EULA](https://vernemq.com/vernemq-licenses.html) | **Only with commercial license** | Off by default; starts only with `VERNEMQ_ACCEPT_EULA=yes`. Commercial use of the official image needs a VerneMQ subscription, or build your own image from source. [MQTT brokers](fleet/mqtt-brokers.md#vernemq) |
| ClickHouse **25.8** (`clickhouse/clickhouse-server:25.8-alpine`) | Optional store for application data (profile `clickhouse`) | [Apache 2.0](https://github.com/ClickHouse/ClickHouse/blob/master/LICENSE) | **Yes** | Unmodified image, reached over its HTTP interface; no ClickHouse library is linked into MeteorCloud. ClickHouse Cloud and its enterprise features are separate products and are not used |
| NATS Server **2.15.3** (`nats:2.15.3-alpine`) | Optional internal messaging for the device event path (profile `nats`) | [Apache 2.0](https://github.com/nats-io/nats-server/blob/main/LICENSE) | **Yes** | Unmodified image, internal only (devices never connect). Synadia's commercial add-ons are separate products and are not used. Re-check the license on every upgrade |
| Filesystem or customer S3 / R2 / GCS | Artifacts | N/A / cloud contract | **Yes** | **Preferred** production storage |
| WireGuard / Docker | VPN / runtime | GPL family / Apache 2.0 | **Yes** | Normal host infra |
| Prometheus / Alertmanager / node_exporter | Metrics / alerts | Apache 2.0 | **Yes** | Optional ops stack; fine |
| kube-prometheus-stack **93.1.1** (Prometheus Operator, kube-state-metrics) | Local k3d monitoring (`make k8s-deploy OBS=1`) | [Apache 2.0](https://github.com/prometheus-community/helm-charts) | **Yes** | Local k3d only, off by default. Its bundled Grafana follows the Grafana row. Production clusters bring their own Prometheus |
| Grafana / Loki / Alloy | Ops dashboards / logs | AGPLv3 | **Yes, with limits** | Optional **operator** tools on **localhost / SSH** only. Off by default. **Not** a customer-facing MeteorCloud UI feature. See [below](#grafana--loki--alloy-yes-with-limits) |
| Data API Go modules: `pgx` (MIT), `prometheus/client_golang` (Apache 2.0), `google/uuid` and `golang.org/x/time` (BSD-3-Clause), `coder/websocket` (ISC), `eclipse/paho.golang` (EPL-2.0 **or** EDL-1.0, BSD-3-Clause style), `nats-io/nats.go`, `nkeys`, `nuid` (Apache 2.0), `klauspost/compress` (BSD-3-Clause / Apache 2.0 / MIT) | Application data plane and device event services (`src/data-plane/go`) | Permissive | **Yes** | Linked into the `data-api` image binaries. `nats-io/nats-server/v2` (Apache 2.0) and its dependencies (`nats-io/jwt`, `minio/highwayhash`, `google/go-tpm`: Apache 2.0; `antithesis-sdk-go`: MIT) are only linked into tests (embedded JetStream server), never shipped. `paho.golang` is used unmodified under its EDL-1.0 option, like the Python `paho-mqtt` the platform already uses |
| `jsonschema` 4.x (with `referencing`, `rpds-py`, `attrs`, `jsonschema-specifications`) and `rfc3339-validator` | Test only: checks `/api/v1/agent/*` answers and agent messages against `contracts/schemas/agent` (control-plane dev extra, `tests/agent-contract`) | [MIT](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING) (all six MIT) | **Yes** | Never shipped in an image or on a device. `tests/agent-contract` also starts the unmodified `eclipse-mosquitto:2` image (Mosquitto row) |
| `debian:12.8-slim` image with Debian `systemd` | Test only: `src/device-plane/agent-go/test/systemd` boots systemd in a container to check the meteorcli units, sockets and permissions | Mixed (systemd LGPL-2.1+, Debian userland GPL family) | **Yes** | Built and run in CI and by developers only; never shipped or pushed. Unmodified Debian packages |
| Python SDK dependencies: `httpx`, `websockets` | `sdk/python` | BSD-3-Clause | **Yes** | Installed by application developers alongside the SDK |
| `pg8000` 1.31 (with `scramp`, `python-dateutil`) | SDK example `sdk/python/examples/stream-to-database` | [BSD-3-Clause](https://github.com/tlocke/pg8000/blob/main/LICENSE) (scramp MIT-0, python-dateutil Apache 2.0 / BSD-3-Clause) | **Yes** | Example only, run by the customer against their own database. Chosen over `psycopg` (LGPL-3.0) to keep the example permissive |
| `golang:1.26-alpine` (build) / `alpine:3.21` (runtime) images | Data API image | BSD-3-Clause (Go) / MIT and GPL-2.0 userland (Alpine, BusyBox) | **Yes** | Unmodified base image; only the static Go binary is added. Same position as the other Alpine-based images |
| `psycopg` 3 (`psycopg[binary]`, control plane) | PostgreSQL driver | [LGPL-3.0](https://github.com/psycopg/psycopg/blob/master/LICENSE.txt) (wheel bundles libpq: PostgreSQL licence) | **Yes** | Used unmodified as a library, installed from PyPI into the image. LGPL lets us ship it in a commercial product as long as it is not modified and can be replaced |
| Terraform **>= 1.6** | Installer provisioning (`make aws-up`, `edge-installer`) | [BSL 1.1](https://github.com/hashicorp/terraform/blob/main/LICENSE) | **Yes, with limits** | A tool the operator installs and runs; MeteorCloud does not ship it. Allowed for provisioning; do not bundle Terraform in a product offered as a competing service. **OpenTofu** (MPL-2.0) works too: the installer uses `tofu` when `terraform` is not installed, or set `EDGE_INSTALLER_TERRAFORM` / `make TERRAFORM=tofu` |
| Ansible | Installer (`make up`) | [GPL-3.0](https://github.com/ansible/ansible/blob/devel/COPYING) | **Yes** | A tool run on the host, not linked into or shipped inside MeteorCloud images |
| Device OS images (Raspberry Pi OS / Debian + Docker + `meteorcli`) | Golden images made with Meteor Imager | Mixed (GPL kernel and userland, Apache 2.0 Docker, Apache 2.0 agent) | **Yes, with obligations** | Handing an image to a customer is redistribution of GPL software: offer the corresponding source (Debian / Raspberry Pi OS source archives) and include [NOTICE](../NOTICE.md) |
| MinIO | S3-compatible artifacts | [AGPLv3](https://github.com/minio/minio) | **No (as default)** | Dev/lab only. The dev image is `bitnamilegacy/minio` (frozen by Bitnami, no updates) and MinIO no longer publishes free images: keep it for local tests only. Production Ansible/Helm default to **filesystem** or real S3. Do not make MinIO the commercial default |
| `cryptography` (Python, control plane) | Encrypts a Wi-Fi password while it waits for its device (AES-GCM) | [Apache-2.0 OR BSD-3-Clause](https://github.com/pyca/cryptography/blob/main/LICENSE) | **Yes** | Permissive. Direct dependency |
| `PyJWT` (Python, control plane) | Signs console access tokens and artifact download tickets (HS256) | [MIT](https://github.com/jpadilla/pyjwt/blob/master/LICENSE) | **Yes** | Replaced `python-jose` (MIT, slow to fix CVEs) |
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
| Mosquitto | `src/data-plane/broker/mosquitto/Dockerfile` (`debian:trixie-slim` + `mosquitto`) | `values.yaml` → `mqtt.mosquitto.image` |
| EMQX | `deploy/compose/data-plane.yml` → `emqx/emqx:5.8.6` | `values.yaml` → `mqtt.emqx.image` |
| VerneMQ | `deploy/compose/data-plane.yml` → `vernemq/vernemq:2.2.1` (`VERNEMQ_IMAGE`) | not in the chart (external only) |
| ClickHouse | `deploy/compose/data-plane.yml` → `clickhouse/clickhouse-server:25.8-alpine` | `clickhouse.enabled` |
| NATS Server | `deploy/compose/data-plane.yml` → `nats:2.15.3-alpine` | `nats.enabled` (image `nats:2.15.3-alpine`) |
| Data API base images | `src/data-plane/go/Dockerfile` → `golang:1.26-alpine`, `alpine:3.21` | not in the chart |
| Data API Go modules | `src/data-plane/go/go.mod` | same |
| Go meteorcli systemd test (test only) | `src/device-plane/agent-go/test/systemd/Dockerfile` → `debian:12.8-slim` | not in the chart |
| MinIO (dev) | `deploy/compose/control-plane.yml` (`MINIO_IMAGE`) | not in the chart |
| Observability | `deploy/compose/docker-compose.observability.yml` | not in the chart |

## Automated check

CI job **Supply chain → Licences of shipped dependencies** (`.github/workflows/supply-chain.yml`) runs
`scripts/check_licenses.py` on the console's production npm packages, the Python services, agent
and SDK (with all their dependencies), and the Go binaries (`go-licenses`). GPL, AGPL, LGPL, SSPL,
BSL/BUSL, Commons Clause, Elastic and unknown licences fail the job unless the package is listed in
`ALLOWED` in that script with a reason (today: `psycopg`, LGPL-3.0, used unmodified). Dev tooling
and container images are not covered; images stay in the table above.

## For coding agents

When adding or upgrading a third-party dependency or image, check this page, state **Production OK**, and surface a **LICENSING RISK** note to the human. Rules: [AGENTS.md](../AGENTS.md), Cursor `.cursor/rules/third-party-licensing.mdc`.

## Upstream references

- [Mosquitto licence](https://github.com/eclipse-mosquitto/mosquitto/blob/master/LICENSE.txt)
- [EMQX Licensing FAQ](https://www.emqx.com/en/content/license-faq), [EMQX 5.8 end-of-life notice](https://www.emqx.com/en/news/a-notice-on-the-emqx-5-8-open-source-version)
- [VerneMQ licences](https://vernemq.com/vernemq-licenses.html)
- [Redis licenses](https://redis.io/legal/licenses/)
- [MinIO / AGPL](https://github.com/minio/minio)
- [Traefik LICENSE (MIT)](https://github.com/traefik/traefik/blob/master/LICENSE.md)
- [PostgreSQL licence](https://www.postgresql.org/about/licence/)
