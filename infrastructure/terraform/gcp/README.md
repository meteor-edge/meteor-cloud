# GCP Cloud Run root stack

Deploys the Edge Platform as Cloud Run services with Cloud SQL and Memorystore.

AWS remains a separate stack under `terraform/aws/` (EC2 + Ansible). This stack does not use SSH, Traefik, or VPN.

## What it creates

| Resource | Purpose |
|----------|---------|
| Artifact Registry (optional) | Docker repo for backend/frontend images |
| VPC + subnet + private service access | Direct VPC egress for Redis and Cloud SQL |
| Cloud SQL PostgreSQL 16 | Application database |
| Memorystore Redis 7 | Rate limiting |
| Secret Manager | JWT, `DATABASE_URL`, `REDIS_URL` |
| Cloud Run backend | FastAPI (`MQTT_ENABLED=false`) |
| Cloud Run frontend | nginx SPA |
| Global HTTP(S) load balancer | Same-origin `/` (UI) and `/api`, `/health` (API) |

MQTT/EMQX is not provisioned: Cloud Run has no public TCP 8883 listener. Device MQTT stays on the local Compose / AWS path until a separate broker is added.

## Images

Cloud Run needs images that already exist. Build and push, then point `backend_image` / `frontend_image` at those URIs.

```bash
REGION=europe-west1
PROJECT=your-gcp-project
AR=$REGION-docker.pkg.dev/$PROJECT/production-app

gcloud auth configure-docker $REGION-docker.pkg.dev
docker build -f infrastructure/docker/Dockerfile.backend -t $AR/backend:0.2.0 .
# From a meteor-ui checkout:
docker build -f infrastructure/docker/Dockerfile.console -t $AR/console:0.2.0 .
docker push $AR/backend:0.2.0
docker push $AR/frontend:0.2.0
```

First apply can create the Artifact Registry repo; push images before Cloud Run can start.

## Variables

Set by the installer via `terraform.tfvars.json` plus sensitive `TF_VAR_postgres_password` and `TF_VAR_jwt_secret`.

Key inputs: `project_id`, `region`, `backend_image`, `frontend_image`, optional `domain` (Google-managed HTTPS cert).

## Outputs

- `platform_url` — `https://<domain>` or `http://<lb-ip>`
- `load_balancer_ip`
- `sql_connection_name`
- `artifact_registry_url`

## Manual plan (debug)

```bash
cp -r ../modules/gcp_cloud_run ./modules/gcp_cloud_run
terraform init
terraform plan -var-file=terraform.tfvars.json
```

Or from repo root: `make terraform-check`.

Do not commit state files.
