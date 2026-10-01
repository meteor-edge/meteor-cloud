# MeteorCloud

Self-hosted Linux fleet platform: operator console, control plane, MQTT data plane, and a device agent. Deploy the application stack on AWS EC2 or GCP Cloud Run. The public website is a separate Next.js app.

## Modules

Each of these is its own process and has its own Compose file under `compose/`.

| Module | Path | Role |
| --- | --- | --- |
| Control plane | `src/control-plane/` | Identity, organizations, device registry, enrollment, MQTT policy and ingest, operator API |
| Data plane | `src/data-plane/` | EMQX platform client: publish, subscribe, forward inbound MQTT to the control plane |
| Console | `console/` | Operator UI. Browser talks only to the control-plane API |
| Device plane | `src/device-plane/agent/` | On-device agent (`meteorcli`) |
| Infrastructure | `infrastructure/` | Terraform, Ansible, installer, Docker, observability |

Same machine: join Compose projects on the Docker network named `meteorcloud`. Across servers: set `DATA_PLANE_URL`, `CONTROL_PLANE_URL`, and `VITE_API_BASE_URL` to reachable origins. The public website is a separate app in [`meteor-edge/meteor-ui`](https://github.com/meteor-edge/meteor-ui); it is not a directory in this repo.

## Local development

```bash
cp .env.example .env
make dev
make seed      # optional: owner@example.com / dev-password-123
```

| Surface | URL |
| --- | --- |
| Console | http://localhost:5173 |
| Control plane | http://localhost:8000 |
| Data plane | http://localhost:8081/health |
| OpenAPI | http://localhost:8000/docs |
| MQTT TLS | mqtts://localhost:8883 |
| EMQX dashboard (dev) | http://localhost:18083 |

Start one module:

```bash
make dev-control-plane
make dev-data-plane
make dev-console
```

Public website (clone `meteor-ui`, not this tree): `make dev-website` inside that repo.

Stop: `make stop`

Product documentation for operators and integrators lives on the website under **Docs**. The operator console links there (`VITE_DOCS_BASE_URL`). Console source is `console/` in this repo. The public website stays in the private repo [`meteor-edge/meteor-ui`](https://github.com/meteor-edge/meteor-ui). Details: [docs/frontends.md](docs/frontends.md).

## Cloud deployment

**AWS (EC2 + Ansible)** — [AWS deployment](docs/aws-deployment.md):

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'
# Edit installation.yaml — provider: aws
make up
```

**GCP (Cloud Run)** — [GCP Cloud Run](docs/gcp-deployment.md):

```bash
cp installer/edge_installer/config/examples/installation.gcp.yaml ./installation.yaml
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'
make up
```

AWS production Compose currently runs the control plane and console on one host. MQTT/EMQX is local Compose (or a host you run yourself). Cloud Run does not expose MQTT TCP 8883. The website is not part of `cloud_app`; deploy it separately.

## Layout

```text
├── src/
│   ├── control-plane/          # FastAPI (app.*)
│   ├── data-plane/             # MQTT gateway (data_plane.*)
│   └── device-plane/agent/     # meteorcli
├── console/                    # operator UI
├── compose/                    # one Compose file per module
├── contracts/                  # HTTP JSON between planes
├── infrastructure/
├── docs/                       # Markdown source (mirrored on the website)
└── Makefile
```
