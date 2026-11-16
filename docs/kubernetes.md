# Kubernetes with Helm

**MeteorCloud does not provision or manage Kubernetes clusters.** The chart in
[`deploy/kubernetes/helm/meteorcloud/`](../deploy/kubernetes/helm/meteorcloud/)
installs MeteorCloud into a cluster you already operate. It contains no cloud
provider APIs, CRDs, operators, or controllers, so the same chart works on k3s,
k3d, GKE, EKS, AKS, OpenShift, and customer clusters. The only exception is
opt-in: `metrics.serviceMonitor` and `metrics.prometheusRule` (off by default)
render Prometheus Operator objects for clusters that already run it
([Observability](observability.md#kubernetes-helm)). On the local k3d cluster,
`make k8s-deploy OBS=1` installs kube-prometheus-stack next to the chart for testing.

## What the chart deploys

| Component | Kind | When |
| --- | --- | --- |
| API (`backend`) | Deployment + Service; init container runs `alembic upgrade head` and the optional first-admin step | always |
| Console | Deployment + Service (nginx) | always |
| Ingress | `/api`, `/health` -> API; `/v1` -> data API when enabled; `/` -> console | `ingress.enabled` (off by default; overlays turn it on) |
| PostgreSQL | StatefulSet + PVC (single instance; optional nightly pg_dump CronJob) | `postgresql.mode=bundled` (default) |
| Artifact volume | PVC | `objectStorage.provider=filesystem` (default) |
| Data plane | Deployment (1 replica) | `mqtt.enabled` |
| Application data API | Deployment + Service (`/v1` on 8082; ops on 8092 ClusterIP only) | `dataApi.enabled` |
| MQTT broker (Mosquitto, or EMQX with `mqtt.provider=emqx`) | Deployment + Service (LoadBalancer, 8883 TLS) | `mqtt.enabled` and `mqtt.broker=bundled` |
| ClickHouse | StatefulSet + Service (HTTP 8123) | `clickhouse.enabled` |
| NATS JetStream | StatefulSet + Service (4222/8222 ClusterIP) | `nats.enabled` |
| device-ingest / rule-processor / clickhouse-writer | Deployments (same `dataApi.image`) | `nats.enabled` (needs `mqtt.enabled` and ClickHouse) |
| Redis | Deployment (no persistence) | `cache.provider=redis` and `cache.redis.mode=bundled` |
| Apps proxy | Traefik Deployment + LoadBalancer Service + PVC (ACME) for public app hostnames | `connectivity.apps.proxy.enabled` |
| Test pod | `helm test` hook | `helm test` |

No MinIO or Kafka. ClickHouse and NATS are optional (`clickhouse.enabled` / `nats.enabled`).

Configuration is split as follows:
- **Application settings** go into a ConfigMap.
- **Secrets** go into a Secret. The chart creates it and generates any missing values (they are kept across upgrades). Alternatively, set `secrets.existingSecret` to provide your own; the required keys are listed in `values.yaml`.
- **Infrastructure choices** are the values themselves.

The chart refuses to render unsupported combinations, for example several API replicas on a ReadWriteOnce artifact volume.

## Values files

| File | Use |
| --- | --- |
| `values.yaml` | Defaults: bundled PostgreSQL, filesystem storage, in-memory rate limits, MQTT off, ingress off |
| `values-dev.yaml` | Local and dev (one model): local images, bundled PostgreSQL and MQTT broker, application data API (`dataApi.enabled`), Traefik ingress |
| `values-staging.yaml` | Example staging: external PostgreSQL, S3, existing Secret, one replica, MQTT off |
| `values-production.yaml` | Example production: external PostgreSQL, S3, Redis, existing Secret, two replicas, TLS ingress |
| `values-ci.yaml` | Pull-request CI only: minimal plus the data API on `/v1` (no Redis, MinIO, MQTT, Kafka, ClickHouse) |

Environments are overlays on one chart (local/dev, then staging, then production):

```bash
helm upgrade --install meteorcloud deploy/kubernetes/helm/meteorcloud \
  -n meteorcloud --create-namespace \
  -f deploy/kubernetes/helm/meteorcloud/values-staging.yaml   # or values-dev / values-production
```

Copy the staging or production file, replace the `example.com` placeholders, and
create the Secret it references before installing. Never commit real secrets.

## Local cluster (k3d)

```bash
make k8s-up && make k8s-deploy
open http://localhost:8088          # admin@example.com / LocalAdmin123!
make k8s-test                       # helm test + scripts/smoke_test.py
make k8s-down
```

`./scripts/k8s-smoke.sh` is what CI runs. It creates a throwaway cluster,
installs with `values-ci.yaml`, runs `helm test` and the smoke test through the
ingress, uninstalls, and deletes the cluster.

## Production prerequisites

You provide:

1. **A cluster** (Kubernetes 1.25+) and `kubectl`/`helm` access to a namespace.
2. **Images.** The chart defaults to the signed release images on
   `ghcr.io/meteor-edge/meteorcloud-*` at the chart `appVersion`; see
   [Release images](release-images.md) to verify them and pin digests. To build your own,
   use `src/control-plane`, `src/data-plane`, `src/data-plane/go` (application data API),
   `console`, and the Mosquitto broker image from `src/data-plane/broker/mosquitto`
   (set `mqtt.mosquitto.image`). Set `imagePullSecrets` if your registry is private.
3. **PostgreSQL 14+**, reachable from the cluster (Cloud SQL, RDS, Azure Database,
   or self-hosted). Put the SQLAlchemy URL in `DATABASE_URL`, for example
   `postgresql+psycopg://user:pass@host:5432/edge_platform?sslmode=require`.
4. **Artifact storage**: either an S3-compatible bucket, or a ReadWriteMany
   StorageClass if you run more than one API replica.
5. **An ingress controller** and TLS certificates (for example cert-manager).
6. **For devices (MQTT)**: a way to expose TCP 8883 (a cloud LoadBalancer,
   MetalLB, or k3s servicelb), a DNS name for `mqtt.publicHost`, and a TLS Secret
   with `ca.crt`, `server.crt`, and `server.key`. The server certificate needs
   SANs for the public host and for the broker Service (`<fullname>-mqtt`, or `<fullname>-emqx` with `mqtt.provider=emqx`; `<fullname>` is `<release>-meteorcloud` (or `<release>-<nameOverride>`), just `<release>` when the release name already contains that name, or `fullnameOverride` when set; Helm cuts it to 63 characters; for release `fleet` that is `fleet-meteorcloud-mqtt`), because the data plane
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
  --from-literal=DATA_PLANE_INTERNAL_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DATAPLANE_DATABASE_URL='postgres://...' \
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

## Application data API (SDK)

The Go data API (`src/data-plane/go`, image `meteorcloud/data-api`) serves the
SDK's publish, subscribe, query and commands on `/v1`. It is off by default:

```yaml
dataApi:
  enabled: true
  bus: memory        # one replica; mqtt fans out through the broker (needs mqtt.enabled)
  storage: postgres  # schema "dataplane" in the platform database
```

- **Internal token.** The API and the data API share `DATA_PLANE_INTERNAL_TOKEN`
  (policy feed and commands). The chart generates it, or takes
  `secrets.dataPlaneInternalToken`. With `secrets.existingSecret`, add
  `DATA_PLANE_INTERNAL_TOKEN` and `DATAPLANE_DATABASE_URL` (`postgres://...`, not
  `postgresql+psycopg://`) to that Secret; `helm install` stops if either is missing.
- **Endpoint.** The ingress routes `/v1` to the data API's port 8082, so
  applications use the console URL: `METEORCLOUD_ENDPOINT=https://fleet.example.com`.
  The ops port 8092 (`/readyz`, `/metrics`, the internal API) is never routed.
- **Direct MQTT** for applications is optional; the SDK works over HTTPS and
  WebSocket with the default Mosquitto broker. It needs `dataApi.bus=mqtt`,
  `mqtt.provider=emqx` and `dataApi.mqtt.direct=true`. A bundled EMQX gets the JWT
  authenticator automatically; for an external EMQX, add a JWT authenticator
  (HMAC, `from = password`, `acl_claim_name = "acl"`) with the same
  `DATAPLANE_MQTT_JWT_SECRET`.

## Device WireGuard (gateway VM)

The WireGuard server never runs in the cluster. It runs on its own AWS or
on-prem VM, set up by `deploy/ansible/playbooks/wireguard.yml`
(see [fleet/connectivity.md](fleet/connectivity.md)). The chart only connects
to it:

1. Run the playbook with `vpn_control_plane_url` set to the private address of
   `connectivity.gatewayService` (an internal load balancer). It writes
   `deploy/ansible/artifacts/wireguard-<gateway>/helm-values.yaml`.
2. Give the pods a route to the device network (`10.8.0.0/16`) through the
   gateway with `vpn_routed_cidrs`. On EKS, pod IPs are VPC IPs: disable the
   gateway's source/dest check and route the device network to its ENI.
3. Create the browser SSH key Secret (skip it with `connectivity.shell.provider: none`):

   ```bash
   ssh-keygen -t ed25519 -N "" -C meteorcloud-shell-gateway -f id_ed25519
   kubectl -n meteorcloud create secret generic meteorcloud-shell \
     --from-file=id_ed25519 --from-file=id_ed25519.pub
   ```

4. Install with the generated values and the gateway's token:

   ```bash
   helm upgrade --install meteorcloud deploy/kubernetes/helm/meteorcloud -n meteorcloud \
     -f my-values.yaml -f deploy/ansible/artifacts/wireguard-<gateway>/helm-values.yaml \
     --set-string secrets.connectivityInternalToken="$EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN"
   ```

   With `secrets.existingSecret`, put `CONNECTIVITY_INTERNAL_TOKEN` in that Secret instead.

Set `connectivity.apps.baseDomain` to the domain of your public app hostnames.

### Public app hostnames

The generic `ingress` serves the console and API only. App endpoints
(`<app>.<device>.<org>.<baseDomain>`) need the optional apps proxy, a Traefik
Deployment that works the same way as the Compose `traefik` service. It polls
the backend's routing document and gets wildcard certificates over DNS-01:

```yaml
connectivity:
  apps:
    baseDomain: apps.example.com
    proxy:
      enabled: true
      acme:
        email: ops@example.com
        dnsProvider: route53
        existingSecret: meteorcloud-dns   # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
```

- Point `*.<baseDomain>` (and `<baseDomain>`) at the `<release>-apps-proxy`
  LoadBalancer Service.
- The proxy pods connect to device WireGuard IPs, so they need the same route
  to the device network as browser SSH (step 2 above).
- With IRSA, leave `acme.existingSecret` empty and give `serviceAccount` a role
  that allows Route53 changes for the zone.
- Certificates live on a small PVC (`helm.sh/resource-policy: keep`), so a
  reinstall doesn't hit Let's Encrypt rate limits. Set
  `acme.caServer: https://acme-staging-v02.api.letsencrypt.org/directory`
  while testing.
- The proxy authenticates to the routing document with
  `CONNECTIVITY_INTERNAL_TOKEN`.

## Staging options

- **k3s on a VM.** Install k3s (`curl -sfL https://get.k3s.io | sh -`). It includes
  Traefik and servicelb, so `values-dev.yaml`-style settings work: set
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

- Bundled PostgreSQL is a single instance without HA. Backups are off unless you set `postgresql.bundled.backup.enabled=true` ([Backups and restore](backups.md)); use an external database for production data.
- The data plane runs as one replica, because MQTT subscriptions are not shared.
- The bundled broker is a single node, and its sessions and retained messages are not persisted.
- `cache.provider=memory` keeps rate-limit counters per pod.
- Migrations run in each API pod's init container. With several replicas starting at the same time, one may retry while another migrates.
- Images are not published by this repository; you build and push them.
