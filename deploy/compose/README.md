# Docker Compose

The single definition of the Compose stack, used by `make dev`, by hand on a
server, and by Ansible (which copies this directory to `/opt/edge-platform/compose`).

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Includes the three module files below |
| `control-plane.yml` | `backend`; bundled `postgres`, `redis`, `minio` behind profiles |
| `data-plane.yml` | `data-plane` (profile `mqtt`), bundled `emqx` (profile `emqx`) |
| `console.yml` | Vite dev server for the console |
| `docker-compose.dev.yml` | Source mounts and hot reload |
| `docker-compose.prod.yml` | Production: Traefik (80/443, Let's Encrypt), nginx console, restart policies, no internal ports published |
| `docker-compose.observability.yml` | Prometheus, Loki, Alloy, Grafana (127.0.0.1 only) |
| `.env.example` | Every setting, grouped into infrastructure, secrets, and application settings |
| `config/`, `traefik/` | EMQX, PostgreSQL init, observability, and Traefik routing config |

```bash
cp .env.example .env          # never commit .env
docker compose up -d --build                                              # development
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --wait   # production
```

`COMPOSE_PROFILES` chooses the bundled services. With no profiles, only the API,
console, and (in production) Traefik run, and every dependency is external.
See [docs/deployment.md](../../docs/deployment.md).
