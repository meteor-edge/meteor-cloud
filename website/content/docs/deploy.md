---
title: Deploy
description: AWS EC2, GCP Cloud Run, and the public website.
section: Operations
order: 30
---

The **installer** (`edge-installer` / `make up`) deploys enabled **services** from `installation.yaml`:

- `cloud_app` — control plane + console + Postgres + Redis (AWS: Docker/Traefik on EC2; GCP: Cloud Run)
- `vpn` — WireGuard on the EC2 host only

That is not the same split as local Compose modules. AWS `cloud_app` today does not start EMQX or the data plane. MQTT is local Compose or a broker you run. Cloud Run does not expose MQTT 8883.

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'
make up
```

Details: in-repo `docs/aws-deployment.md` and `docs/gcp-deployment.md`.

## Website

The public site is **not** part of `cloud_app`. Run `compose/website.yml` or the `Dockerfile.website` image on any host. Set `SITE_URL` to the public origin (sitemap and canonical URLs).

Content is Markdown under `website/content` (`WEBSITE_CONTENT_SOURCE=filesystem`). Later, a CMS can set `WEBSITE_CONTENT_SOURCE=database` and use a **website-owned** database — never the control-plane Postgres.
