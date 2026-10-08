# MeteorCloud

Self-hosted Linux fleet platform: operator console, control plane, MQTT data plane, and a device agent. The public website is a separate Next.js app in [`meteor-edge/meteor-ui`](https://github.com/meteor-edge/meteor-ui).

## Modules

| Module | Path | Role |
| --- | --- | --- |
| Control plane | `src/control-plane/` | Identity, organizations, device registry, enrollment, artifacts, MQTT policy and ingest, operator API |
| Data plane | `src/data-plane/` | EMQX platform client: publish, subscribe, forward inbound MQTT to the control plane |
| Console | `console/` | Operator UI. Browser talks only to the control-plane API |
| Device plane | `src/device-plane/agent/` | On-device agent (`meteorcli`); runs on devices, not deployed by MeteorCloud |

## Deployment

There are two independent ways to run MeteorCloud. Both use the same container images and the same settings.

| | Docker Compose + Ansible | Kubernetes + Helm |
| --- | --- | --- |
| For | One VM, EC2 instance, or on-prem server | An existing cluster: k3s, k3d, GKE, EKS, AKS, OpenShift, customer clusters |
| Files | [`deploy/compose/`](deploy/compose/), [`deploy/ansible/`](deploy/ansible/) | [`deploy/kubernetes/helm/meteorcloud/`](deploy/kubernetes/helm/meteorcloud/) |
| Install | `ansible-playbook playbooks/site.yml` (or `edge-installer apply` on AWS) | `helm upgrade --install` |
| Guide | [docs/deployment.md](docs/deployment.md) | [docs/kubernetes.md](docs/kubernetes.md) |

**MeteorCloud does not provision or manage Kubernetes clusters.** The Helm chart installs into a cluster you already have. Creating clusters, node pools, load balancers, DNS, and managed databases is your platform's job (Terraform, eksctl, gcloud, the cloud console, ...). k3d is used only as a local development and CI tool.

Ansible only prepares servers and runs Docker Compose; it never deploys to Kubernetes. Terraform ([`infrastructure/terraform/aws`](infrastructure/terraform/aws/)) only creates the AWS EC2 host for the Compose path.

On AWS, `make up` (or `edge-installer apply`) runs Terraform then Ansible. To prove a fresh instance still installs, run GitHub Actions → **EC2 smoke test** (manual; needs `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`). Details: [docs/aws-ci.md](docs/aws-ci.md). Helm is a separate path for clusters you already have (`values-dev.yaml`, `values-staging.yaml`, `values-production.yaml`).

### Dependencies

| Dependency | Status | Options |
| --- | --- | --- |
| PostgreSQL | Required | Bundled container, or external (managed or customer database) |
| Artifact storage | Required | Filesystem volume, AWS S3, any S3-compatible API (MinIO, Ceph, R2, GCS interoperability, customer storage). Bundled MinIO is optional and off by default |
| MQTT broker | Optional (device connectivity) | Bundled EMQX, or an external EMQX; off by default |
| Redis | Optional | Only for shared rate limits across several API replicas; in-memory otherwise |
| Kafka, ClickHouse | Not used | Not deployed |

The smallest install runs PostgreSQL, the API, the console, and a reverse proxy (Traefik with Compose, your ingress controller with Kubernetes).

## Local development

Docker Compose (hot reload):

```bash
cp deploy/compose/.env.example deploy/compose/.env   # make dev does this if missing
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

Choose bundled services with `COMPOSE_PROFILES` in `deploy/compose/.env` (`postgres`, `redis`, `minio`, `mqtt`, `emqx`). Start one module: `make dev-control-plane`, `make dev-data-plane`, `make dev-console`. Stop: `make stop`.

Kubernetes (k3d + Helm, needs [k3d](https://k3d.io), kubectl, and helm):

```bash
make k8s-up       # local k3d cluster: ingress on localhost:8088, MQTT on localhost:18883
make k8s-deploy   # build images, import into k3d, helm upgrade --install (values-dev.yaml)
make k8s-status   # pods, services, ingress, volumes
make k8s-test     # helm test + HTTP smoke test
make k8s-down     # delete the cluster
```

Checks that CI runs: `make test lint typecheck compose-config compose-smoke helm-lint terraform-check ansible-check ansible-lint`, and `./scripts/k8s-smoke.sh` for a throwaway-cluster install. See [docs/development.md](docs/development.md).

Product documentation for operators lives on the website under **Docs**; the console links there (`VITE_DOCS_BASE_URL`). Details: [docs/frontends.md](docs/frontends.md).

## Layout

```text
├── src/
│   ├── control-plane/          # FastAPI (app.*), Dockerfile
│   ├── data-plane/             # MQTT gateway (data_plane.*), Dockerfile
│   └── device-plane/agent/     # meteorcli
├── console/                    # operator UI, Dockerfile (nginx)
├── deploy/
│   ├── compose/                # Docker Compose files, .env.example, service config
│   ├── ansible/                # server preparation + Compose deployment
│   └── kubernetes/helm/        # Helm chart
├── infrastructure/
│   ├── terraform/aws/          # EC2 host for the Compose path
│   └── installer/              # edge-installer (Terraform + Ansible on AWS)
├── contracts/                  # HTTP JSON between planes
├── scripts/                    # smoke tests, certificates, CI helpers
├── docs/
└── Makefile
```
