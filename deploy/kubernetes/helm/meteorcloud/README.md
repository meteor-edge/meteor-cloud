# meteorcloud Helm chart

Installs MeteorCloud (API, console, and optionally the MQTT data plane and a broker)
into an existing Kubernetes cluster. The chart does not create clusters or any
cloud resources.

```bash
helm upgrade --install meteorcloud deploy/kubernetes/helm/meteorcloud \
  -n meteorcloud --create-namespace -f my-values.yaml --wait
helm -n meteorcloud test meteorcloud
```

| Setting | Options | Default |
|---------|---------|---------|
| `postgresql.mode` | `bundled` (StatefulSet), `external` | `bundled` |
| `objectStorage.provider` | `filesystem` (PVC), `s3` (any S3-compatible API) | `filesystem` |
| `cache.provider` | `memory`, `redis` (`cache.redis.mode`: `bundled` / `external`) | `memory` |
| `mqtt.enabled`, `mqtt.broker` | off, or `bundled` / `external` | off |
| `mqtt.provider` | `mosquitto`, `emqx` (bundled or external), `vernemq` (external only) | `mosquitto` |
| `ingress.enabled`, `ingress.className` | any ingress controller | disabled; set `ingress.enabled: true` in an overlay |
| `secrets.existingSecret` | your own Secret instead of the generated one | empty |
| `networkPolicy.enabled` | only this release, the ingress namespace, and `networkPolicy.extraFrom` reach the API (needs a CNI that enforces it) | off |
| `connectivity.provider` | `none`, `wireguard` (gateway on its own VM, see below) | `none` |
| `metrics.enabled` | `/metrics` on the API and data plane; `false` records nothing and returns 404 | on |
| `metrics.serviceMonitor.enabled`, `metrics.prometheusRule.enabled` | Prometheus Operator objects (job `backend` / `data-plane`, the bundled alert rules) | off |

Device WireGuard (browser SSH, app endpoints): the WireGuard server runs outside
the cluster on an AWS or on-prem VM set up by
[`deploy/ansible/playbooks/wireguard.yml`](../../../ansible/README.md#wireguard-gateway),
which writes the `connectivity.*` values to `artifacts/wireguard-<gateway>/helm-values.yaml`.
`connectivity.gatewayService` adds an internal load balancer the gateway agent polls;
give the gateway the same `secrets.connectivityInternalToken`. Browser SSH
(`connectivity.shell.provider: ssh_gateway`) needs a Secret with the gateway key
(`connectivity.shell.existingSecret`) and a route from the pods to the device network.
See [docs/kubernetes.md](../../../../docs/kubernetes.md#device-wireguard-gateway-vm).

Metrics: the chart does not install Prometheus, Grafana or Loki; use your
cluster's monitoring. The rules in `files/rules/` are a copy of the Compose bundle
(a test keeps them identical) and the dashboards are in
`deploy/compose/config/observability/grafana/provisioning/dashboards/json/`.
See [docs/observability.md](../../../../docs/observability.md#kubernetes-helm).

Every value is documented in [`values.yaml`](values.yaml). Environment overlays,
all on this one chart:

| File | Environment |
| --- | --- |
| `values-dev.yaml` | Local and dev (k3d or a shared dev cluster) |
| `values-staging.yaml` | Staging example (external PostgreSQL, S3, one replica) |
| `values-production.yaml` | Production example (external PostgreSQL, Redis, S3, two replicas) |
| `values-ci.yaml` | Pull-request CI only |
| `values-monitoring.yaml` | Add-on for clusters with the Prometheus Operator (combine with any of the above) |

```bash
helm upgrade --install meteorcloud deploy/kubernetes/helm/meteorcloud \
  -n meteorcloud --create-namespace \
  -f deploy/kubernetes/helm/meteorcloud/values-staging.yaml   # or values-dev / values-production
```

Full guide, prerequisites, and per-provider notes: [docs/kubernetes.md](../../../../docs/kubernetes.md).
