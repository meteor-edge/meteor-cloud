# GCP Cloud Run deployment

Deploy the Edge Platform to Google Cloud Run alongside the existing AWS EC2 path.

AWS stays: one VM, Docker Compose, Traefik, optional WireGuard, Ansible.
GCP is a different shape: Cloud Run + Cloud SQL + Memorystore + a global HTTP(S) load balancer. No SSH, no Ansible, no VPN.

MQTT/EMQX is **not** included. Cloud Run does not expose TCP 8883. Device MQTT continues to work on local Compose and AWS.

## Prerequisites

- Terraform 1.5+
- `gcloud` CLI (recommended)
- A GCP project with billing
- Application Default Credentials:

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT
```

Or set `GOOGLE_APPLICATION_CREDENTIALS` to a service account JSON key (do not commit it).

Required APIs (Terraform enables them when `gcp.enable_apis` is true): Run, Cloud SQL, Redis, Secret Manager, Artifact Registry, Compute, Service Networking, IAM.

## Images

Push backend and frontend images **before** Cloud Run can become healthy. First apply can create Artifact Registry; then build/push; then apply again (or push first if the repo already exists).

```bash
REGION=europe-west1
PROJECT=your-gcp-project
AR=$REGION-docker.pkg.dev/$PROJECT/production-app

gcloud auth configure-docker $REGION-docker.pkg.dev
docker build -f platform/docker/Dockerfile.backend -t $AR/backend:0.2.0 .
docker build -f infrastructure/docker/Dockerfile.console -t $AR/console:0.2.0 .
docker push $AR/backend:0.2.0
docker push $AR/frontend:0.2.0
```

## Configure

```bash
cp installer/edge_installer/config/examples/installation.gcp.yaml ./installation.yaml
```

Set `gcp.project_id`, `gcp.region`, and `deployment.backend_image` / `frontend_image`.

```yaml
installation:
  provider: gcp

gcp:
  project_id: your-gcp-project
  region: europe-west1

services:
  cloud_app:
    enabled: true
  vpn:
    enabled: false   # required — VPN is AWS-only
```

Optional `platform.domain`: Terraform requests a Google-managed certificate and HTTPS on the load balancer. Point DNS A record at the outputted load balancer IP.

## Secrets

Same env vars as AWS:

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'
```

They are passed to Terraform as `TF_VAR_*` (not written into `terraform.tfvars.json`) and stored in Secret Manager.

## Deploy

```bash
edge-installer validate installation.yaml
make plan
make up
make down
```

What `apply` does on GCP:

1. Validates config, secrets, Terraform, GCP credentials
2. Terraform: VPC, Cloud SQL Postgres, Memorystore Redis, Secret Manager, Cloud Run, global load balancer
3. Health check against `platform_url` (`/health` and `/`)

No SSH wait and no Ansible.

## URL layout

The load balancer is same-origin, like Traefik on AWS:

| Path | Service |
|------|---------|
| `/` | Frontend |
| `/api/*`, `/health`, `/docs`, `/metrics` | Backend |

Without a domain the URL is `http://<load-balancer-ip>`. Set `platform.public_url` to that URL after the first apply if browsers need an explicit CORS origin (otherwise CORS is `*`).

## Limitations

- No EMQX / MQTT TLS
- No WireGuard VPN
- No Prometheus/Loki on the instance (Cloud Run has Cloud Logging)
- Cloud SQL instance names cannot be reused for about a week after destroy (Terraform adds a random suffix)
- `db-f1-micro` is the default tier; raise `gcp.sql_tier` for production

## Manual Terraform

```bash
cd infrastructure/terraform/gcp
cp -r ../modules/gcp_cloud_run ./modules/gcp_cloud_run
terraform init
terraform plan -var-file=/path/to/terraform.tfvars.json
```
