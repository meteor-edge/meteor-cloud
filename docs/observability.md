# Observability

The control plane always emits:

- **JSON or console logs** on stdout (`LOG_FORMAT=json` or `console`)
- **Prometheus metrics** at `GET /metrics` (process CPU/RSS and HTTP timings; `/health` is excluded)
- **Audit events** in Postgres (`GET /api/v1/organizations/{org}/audit-events` for owners/admins)

It does not talk to Loki or CloudWatch. Collectors are optional.

## Default stack (on-prem)

Prometheus (metrics) + node_exporter (host CPU/memory) + Grafana Alloy → Loki (logs) + Grafana (UI). Docker API access goes through a read-limited socket proxy; Alloy does not mount the host Docker socket.

```bash
make observability
```

- Grafana: http://localhost:3001 (credentials via `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` env vars)
- Prometheus: http://localhost:9090
- App metrics: http://localhost:8000/metrics

`make dev` does **not** start this stack. The files are `deploy/compose/docker-compose.observability.yml` and `deploy/compose/config/observability/`; Prometheus and Grafana bind to 127.0.0.1. `GRAFANA_ADMIN_PASSWORD` is required.

## CloudWatch

Keep the config switch for later:

```yaml
observability:
  enabled: false
  backend: prometheus   # or cloudwatch (not implemented yet)
```

`backend: cloudwatch` with `enabled: true` is rejected at installer validate until a CloudWatch agent is wired.

## AWS install

Set `observability.enabled: true` and `backend: prometheus` in `installation.yaml` (with Ansible directly: `observability_enabled: true` and `EDGE_PLATFORM_GRAFANA_ADMIN_PASSWORD`). Grafana binds to `127.0.0.1:3001` on the instance (SSH tunnel; not on Traefik). Skip on `t3.small` if RAM is tight.

Alerts (Alertmanager / CloudWatch Alarms) are not in this cut.
