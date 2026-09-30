---
title: Get started
description: Run MeteorCloud locally with Docker Compose.
section: Overview
order: 3
---

You need Docker Compose, Python 3.13+, Node.js 22+, and Make.

```bash
cp .env.example .env
make install
make dev
make seed
```

`make seed` creates `owner@example.com` / `dev-password-123`.

| Surface | URL |
| --- | --- |
| Website | http://localhost:3000 |
| Console | http://localhost:5173 |
| Control plane | http://localhost:8000 |
| OpenAPI | http://localhost:8000/docs |
| Data plane | http://localhost:8081/health |
| MQTT | mqtts://localhost:8883 |

One module at a time:

```bash
make dev-control-plane
make dev-data-plane
make dev-console
make dev-website
```

Join rules for the same host: every Compose project must use the network name `meteorcloud`. The website does not need the API. The console does.

Without Compose between processes, point `DATA_PLANE_URL=http://127.0.0.1:8081` and `CONTROL_PLANE_URL=http://127.0.0.1:8000`.
