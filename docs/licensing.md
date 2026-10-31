# Third-party component licensing

Operator guidance for the **bundled** infrastructure images MeteorCloud can
start. This is not legal advice. Confirm your product model (self-hosted for
yourself vs SaaS vs redistributing MeteorCloud) with counsel when in doubt.

Application code you build (control plane, data plane, console, `meteorcli`)
is separate from these vendor images. Prefer **ports/adapters** so a component
can be swapped for an external managed service.

## Summary

| Component | How we use it | Image / pin | License posture | Self-host OK? | Notes |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL | Required control-plane DB | `postgres:16-alpine` | [PostgreSQL License](https://www.postgresql.org/about/licence/) (permissive) | Yes | Preferred default; external managed Postgres also fine |
| Traefik | Reverse proxy / TLS | `traefik:v3.3` | [MIT](https://github.com/traefik/traefik/blob/master/LICENSE.md) | Yes | Open-source Traefik Proxy only; Hub features need a Traefik commercial token |
| Redis | Optional rate-limit / cache | `redis:7.2-alpine` | [BSD-3-Clause](https://redis.io/legal/licenses/) for ≤7.2 | Yes | Do **not** use floating `redis:7` / `7-alpine` (tracks 7.4+). Redis 7.4+ is RSALv2/SSPLv1; Redis 8+ is RSAL/SSPL/AGPL. Off by default (in-memory limits) |
| EMQX | Optional MQTT broker | `emqx/emqx:5.8.6` | Apache 2.0 (5.8 OSS) | Yes (single node as shipped) | Details: [MQTT → EMQX licensing](fleet/mqtt.md#emqx-version-and-licensing). 5.9+ is BSL 1.1 |
| MinIO | Optional S3-compatible artifacts (mostly **dev**) | `bitnamilegacy/minio:…` | Upstream MinIO is [AGPLv3](https://github.com/minio/minio) | Dev: yes with care. Production commercial: prefer not | Production Ansible/Helm default to **`filesystem`** or real S3/R2/GCS. AGPL has network-copyleft obligations; MinIO’s community binary story also changed (EOL / AIStor). Do not treat bundled MinIO as the production path for a commercial SaaS |
| WireGuard | Optional device VPN | Host packages (`wireguard`) | Kernel + tools: GPL family | Yes | Standard Linux VPN; not linked into the Python app |
| Docker Engine | Host runtime | Distro packages | Apache 2.0 (Moby) | Yes | Install via Ansible on the VM |

Optional **observability** Compose overlay (`deploy/compose/docker-compose.observability.yml`) is not part of the default product install:

| Component | License | Note |
| --- | --- | --- |
| Prometheus, node-exporter | Apache 2.0 | Fine for self-host |
| Grafana, Loki, Alloy | AGPL (Grafana Labs) | Optional lab stack only; review AGPL before offering them as part of a commercial service |

## What “right” looks like for MeteorCloud

**Safe defaults we already lean on**

- Required DB is PostgreSQL (permissive).
- Production artifacts: `filesystem` or customer/AWS S3 — not MinIO ([Artifacts](fleet/artifacts.md)).
- Redis and MQTT/EMQX are **optional** and off unless enabled.
- Bundled EMQX is pinned to **5.8.6** (Apache 2.0), single instance.
- Bundled Redis is pinned to **7.2-alpine** (BSD-3-Clause).

**Watch items**

1. **Do not casually bump EMQX past 5.8** — BSL 1.1 and commercial limits for clusters / hosted-or-embedded use. See [MQTT](fleet/mqtt.md#emqx-version-and-licensing).
2. **Do not use floating `redis:7-alpine`** — it currently resolves to Redis **7.4.x** (RSALv2/SSPLv1). Stay on `7.2-alpine` or move to a BSD fork (e.g. Valkey) if you need a newer line under a permissive license.
3. **Avoid MinIO in commercial production** unless counsel is comfortable with AGPLv3 (or you buy MinIO AIStor / use another object store). Prefer `OBJECT_STORAGE_PROVIDER=filesystem` or cloud S3.
4. **External “bring your own” services** (managed Postgres, ElastiCache, EMQX Cloud, S3) shift licensing to that vendor’s contract — often simpler for SaaS.

## Pin locations

| Component | Compose | Helm |
| --- | --- | --- |
| PostgreSQL | `deploy/compose/control-plane.yml` | `values.yaml` → `postgres.bundled.image` |
| Redis | `deploy/compose/control-plane.yml` | `values.yaml` → `cache.redis.bundled.image` |
| Traefik | `deploy/compose/docker-compose.prod.yml` | `values.yaml` → `ingress.traefik.image` |
| EMQX | `deploy/compose/data-plane.yml` | `values.yaml` → `mqtt.emqx.image` |
| MinIO | `deploy/compose/control-plane.yml` (`MINIO_IMAGE`) | not deployed by the chart |

## Upstream references

- [EMQX Licensing FAQ](https://www.emqx.com/en/content/license-faq)
- [Redis licenses](https://redis.io/legal/licenses/)
- [MinIO / AGPL notice](https://github.com/minio/minio)
- [Traefik LICENSE (MIT)](https://github.com/traefik/traefik/blob/master/LICENSE.md)
- [PostgreSQL licence](https://www.postgresql.org/about/licence/)
