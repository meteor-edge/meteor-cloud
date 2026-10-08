# meteorcloud Helm chart

Installs MeteorCloud (API, console, and optionally the MQTT data plane and EMQX)
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
| `mqtt.enabled`, `mqtt.broker` | off, or `bundled` EMQX / `external` | off |
| `ingress.enabled`, `ingress.className` | any ingress controller | enabled, cluster default class |
| `secrets.existingSecret` | your own Secret instead of the generated one | empty |

Every value is documented in [`values.yaml`](values.yaml). Example files:
`values-local.yaml` (k3d), `values-ci.yaml` (CI), `values-production.yaml`.

Full guide, prerequisites, and per-provider notes: [docs/kubernetes.md](../../../../docs/kubernetes.md).
