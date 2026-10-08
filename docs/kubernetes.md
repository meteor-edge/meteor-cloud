# Kubernetes with Helm

**MeteorCloud does not provision or manage Kubernetes clusters.** The chart in
[`deploy/kubernetes/helm/meteorcloud/`](../deploy/kubernetes/helm/meteorcloud/)
installs MeteorCloud into a cluster you already operate. It contains no cloud
provider APIs, CRDs, operators, or controllers, so the same chart works on k3s,
k3d, GKE, EKS, AKS, OpenShift, and customer clusters.

## What the chart deploys

| Component | Kind | When |
| --- | --- | --- |
| API (`backend`) | Deployment + Service; init container runs `alembic upgrade head` and the optional first-admin step | always |
| Console | Deployment + Service (nginx) | always |
| Ingress | `/api`, `/health` -> API; `/` -> console | `ingress.enabled` (default) |
| PostgreSQL | StatefulSet + PVC (single instance, no backups) | `postgresql.mode=bundled` (default) |
| Artifact volume | PVC | `objectStorage.provider=filesystem` (default) |
| Data plane | Deployment (1 replica) | `mqtt.enabled` |
| EMQX | Deployment + Service (LoadBalancer, 8883 TLS) | `mqtt.enabled` and `mqtt.broker=bundled` |
| Redis | Deployment (no persistence) | `cache.provider=redis` and `cache.redis.mode=bundled` |
| Test pod | `helm test` hook | `helm test` |

No MinIO, Kafka, or ClickHouse.

Configuration is split as follows:
- **Application settings** go into a ConfigMap.
- **Secrets** go into a Secret. The chart creates it and generates any missing values (they are kept across upgrades). Alternatively, set `secrets.existingSecret` to provide your own; the required keys are listed in `values.yaml`.
- **Infrastructure choices** are the values themselves.

The chart refuses to render unsupported combinations, for example several API replicas on a ReadWriteOnce artifact volume.

## Values files

| File | Use |
| --- | --- |
| `values.yaml` | Defaults: bundled PostgreSQL, filesystem storage, in-memory rate limits, MQTT off |
| `values-local.yaml` | k3d development: local images, Traefik ingress, bundled EMQX |
| `values-ci.yaml` | Pull-request CI: minimal (no Redis, MinIO, MQTT, Kafka, ClickHouse) |
| `values-production.yaml` | Example: external PostgreSQL, S3, Redis, existing Secret, TLS ingress |

## Local cluster (k3d)

```bash
make k8s-up && make k8s-deploy
open http://localhost:8088          # admin@meteorcloud.local / LocalAdmin123!
make k8s-test                       # helm test + scripts/smoke_test.py
make k8s-down
```

`./scripts/k8s-smoke.sh` is what CI runs. It creates a throwaway cluster,
installs with `values-ci.yaml`, runs `helm test` and the smoke test through the
ingress, uninstalls, and deletes the cluster.

## Production prerequisites

You provide:

1. **A cluster** (Kubernetes 1.25+) and `kubectl`/`helm` access to a namespace.
2. **Images** in a registry the cluster can pull from. Build them from
   `src/control-plane`, `src/data-plane`, and `console`. Set `imagePullSecrets`
   if the registry is private.
3. **PostgreSQL 14+**, reachable from the cluster (Cloud SQL, RDS, Azure Database,
   or self-hosted). Put the SQLAlchemy URL in `DATABASE_URL`, for example
   `postgresql+psycopg://user:pass@host:5432/edge_platform?sslmode=require`.
4. **Artifact storage**: either an S3-compatible bucket, or a ReadWriteMany
   StorageClass if you run more than one API replica.
5. **An ingress controller** and TLS certificates (for example cert-manager).
6. **For devices (MQTT)**: a way to expose TCP 8883 (a cloud LoadBalancer,
   MetalLB, or k3s servicelb), a DNS name for `mqtt.publicHost`, and a TLS Secret
   with `ca.crt`, `server.crt`, and `server.key`. The server certificate needs
   SANs for the public host and for `<release>-emqx`, because the data plane
   verifies it inside the cluster.
7. **Redis** (optional), for consistent rate limits across API replicas.

Deploy:

```bash
kubectl create namespace meteorcloud
kubectl -n meteorcloud create secret generic meteorcloud-secrets \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=APP_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=DATABASE_URL='postgresql+psycopg://...' \
  --from-literal=REDIS_URL='rediss://...' \
  --from-literal=MQTT_PLATFORM_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=MQTT_INTERNAL_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=EMQX_DASHBOARD_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=EMQX_NODE_COOKIE="$(openssl rand -hex 24)"
kubectl -n meteorcloud create secret generic meteorcloud-mqtt-tls \
  --from-file=ca.crt --from-file=server.crt --from-file=server.key

cp deploy/kubernetes/helm/meteorcloud/values-production.yaml my-values.yaml   # edit
helm upgrade --install meteorcloud deploy/kubernetes/helm/meteorcloud \
  -n meteorcloud -f my-values.yaml --wait
helm -n meteorcloud test meteorcloud
python3 scripts/smoke_test.py https://fleet.example.com
```

Upgrades use the same `helm upgrade --install` with new image tags. Migrations run
in the API's init container before the new version serves traffic.

## Staging options

- **k3s on a VM.** Install k3s (`curl -sfL https://get.k3s.io | sh -`). It includes
  Traefik and servicelb, so `values-local.yaml`-style settings work: set
  `ingress.className: traefik` and point `mqtt.publicHost` at the VM. Push images to
  a registry or import them with `k3s ctr images import`.
- **GKE.** Create the cluster yourself (console, gcloud, or your own Terraform).
  Use the GKE ingress (`ingress.className: gce`) or ingress-nginx, and Cloud SQL
  (through the Cloud SQL Auth Proxy sidecar or private IP) via `DATABASE_URL`. For
  artifacts, use GCS interoperability: set `objectStorage.s3.endpointUrl` to
  `https://storage.googleapis.com` and provide HMAC keys.

## Provider notes

The chart itself is the same everywhere; only values differ.

| Platform | Ingress | Database | Artifacts | MQTT LoadBalancer |
| --- | --- | --- | --- | --- |
| k3s | Traefik (built in) | bundled or external | filesystem (local-path) or S3 | servicelb (built in) |
| GKE | `gce` or ingress-nginx | Cloud SQL | GCS (S3 interop, HMAC) | cloud LB; `loadBalancerSourceRanges` |
| EKS | AWS Load Balancer Controller (`alb`) or ingress-nginx | RDS | S3 with IRSA (`serviceAccount.annotations`) | NLB (`service.beta.kubernetes.io/aws-load-balancer-type` annotation) |
| AKS | ingress-nginx or Application Gateway | Azure Database for PostgreSQL | any S3-compatible service, or Azure Files RWX volume | Azure LB |
| Customer cluster | whatever exists | customer PostgreSQL | customer S3 or RWX volume | MetalLB or existing LB |

## Limitations

- Bundled PostgreSQL is a single instance without backups or HA; use an external database for production data.
- The data plane runs as one replica, because MQTT subscriptions are not shared.
- The bundled EMQX is a single node, and its sessions and retained messages are not persisted.
- `cache.provider=memory` keeps rate-limit counters per pod.
- Migrations run in each API pod's init container. With several replicas starting at the same time, one may retry while another migrates.
- Images are not published by this repository; you build and push them.
