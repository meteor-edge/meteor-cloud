# Observability

MeteorCloud emits metrics and logs; it never depends on a monitoring system. Every
collector, store and UI is optional and replaceable, and the platform behaves the same
when any of them is stopped, missing or pointed somewhere else.

What the services emit:

- **Prometheus metrics** at `GET /metrics` on the API (`:8000`) and the data plane (`:8081`).
  Never routed through Traefik or the Kubernetes ingress.
- **Logs** on stdout, one JSON object per line with `LOG_FORMAT=json` (console text otherwise).
  API lines carry `request_id`; with `ACCESS_LOG` on, each HTTP request also logs one
  `event=http_request` line (method, route template, status, `duration_ms`).
- **Audit events** in Postgres (`GET /api/v1/organizations/{org}/audit-events` for owners and admins).

## Contracts

These are the only things a replacement must honour.

| Contract | Detail |
| --- | --- |
| Metrics | Prometheus text format on `GET /metrics`. Labels are bounded: method, route template, status, result, kind. Never org, device, user, topic or IP. |
| Job names | `job="backend"` for the API, `job="data-plane"` for the data plane. Rules and dashboards rely on them. |
| Logs | stdout, one JSON object per line, with `timestamp`, `level`, `logger`, `event` (and `request_id` on the API). Collectors add a `service` label. |
| Datasources | Dashboards use only the Grafana datasource uids `prometheus` and `loki`. |
| Rules | Standard Prometheus rule files in `deploy/compose/config/observability/rules/` (the Helm chart ships a copy). |

Inside the API, metrics go through a port (`app/ports/observability.py`). The Prometheus
adapter is the only module that imports `prometheus_client`; `METRICS_ENABLED=false` swaps in
a no-op adapter. An OpenTelemetry or StatsD backend would be a new adapter, not a change to
call sites.

## Switches

| Variable | Service | Default | Effect |
| --- | --- | --- | --- |
| `METRICS_ENABLED` | API, data plane | `true` | `false`: nothing is recorded and `/metrics` returns 404. |
| `LOG_FORMAT` | API, data plane | `console`; the observability overlay and Helm set `json` | `json` for collectors. |
| `ACCESS_LOG` | API | on when `LOG_FORMAT=json` | `true` / `false` forces it. |
| `FLEET_METRICS_ENABLED` | API | `true` | Fleet gauges (devices by state, Deploy progress). |
| `FLEET_METRICS_CACHE_SECONDS` | API | `30` | Fleet gauges are computed on scrape, at most once per this many seconds. |

A request can carry its own `X-Request-ID` (1–64 characters of `A-Z a-z 0-9 . _ -`); anything
else is replaced by a generated id. The id is returned in the response header and appears in
every log line for that request.

## KPIs

Counters are summed across API replicas; fleet gauges are the same on every replica, so take
`max`.

| Area | Metric | Labels |
| --- | --- | --- |
| API traffic | `http_requests_total`, `http_request_duration_seconds` | method, path (route template), status |
| Build | `meteorcloud_build_info` | version |
| Fleet | `meteorcloud_devices` | state: online, offline, never_seen |
| | `meteorcloud_devices_disabled`, `meteorcloud_devices_software_unhealthy` | |
| Registration | `meteorcloud_device_registrations_total` | result |
| | `meteorcloud_enrollment_requests_total`, `meteorcloud_enrollment_polls_total` | result / status |
| | `meteorcloud_agent_heartbeats_total` | |
| | `meteorcloud_rate_limited_total` | limiter |
| | `meteorcloud_auth_logins_total` | result |
| Deploy | `meteorcloud_deployments_created_total` | strategy |
| | `meteorcloud_deployments_finished_total`, `meteorcloud_deployment_device_results_total` | status |
| | `meteorcloud_deployments` | status: in_progress, paused |
| | `meteorcloud_deployment_devices` | status (devices waiting on a running Deploy) |
| | `meteorcloud_deploy_sweep_ticks_total` | result (background rollout sweeper) |
| Health | `meteorcloud_database_up` | 1 or 0, from the fleet query (absent when fleet metrics are off) |
| | `meteorcloud_unhandled_exceptions_total` | (requests that crashed, as opposed to a deliberate 5xx) |
| MQTT (API) | `meteorcloud_mqtt_auth_total` | action, result |
| | `meteorcloud_mqtt_messages_ingested_total`, `meteorcloud_mqtt_publish_total` | kind / result |
| | `meteorcloud_device_commands_total` | kind, result |
| Shell | `meteorcloud_shell_sessions_active`, `meteorcloud_shell_sessions_total` | kind (, result) |
| Data plane | `meteorcloud_dataplane_mqtt_connected` | |
| | `meteorcloud_dataplane_mqtt_messages_received_total` | kind |
| | `meteorcloud_dataplane_ingest_forward_total`, `meteorcloud_dataplane_publish_total` | result |

Recording rules (`rules/recording.yml`) precompute the API request rate, 5xx rate and ratio,
and p95 latency per `scope` (`public` = `/api`, `internal` = `/internal`). Downloads, the
MQTT event hook and artifact uploads are left out of the latency rule because they are slow
by design.

## Components

The Compose bundle is `deploy/compose/docker-compose.observability.yml` plus
`deploy/compose/config/observability/`. Each component is a Compose profile:

| Profile | Services | Port (127.0.0.1) | Replace with |
| --- | --- | --- | --- |
| `metrics` | Prometheus, node_exporter | 9090 | Any Prometheus-compatible store: set `PROMETHEUS_URL` for Grafana and scrape the targets yourself. |
| `logs` | Loki, Alloy (via a read-only Docker socket proxy) | 3100 | `LOKI_PUSH_URL` (Alloy) / `LOKI_URL` (Grafana), or any collector that reads container stdout. |
| `alerting` | Alertmanager | 9093 | Any Alertmanager-compatible receiver. Needs `metrics`. |
| `dashboards` | Grafana | 3001 | Import the JSON dashboards into your own Grafana. |

Prometheus finds the data plane and Alertmanager through DNS, so it does not error when the
`mqtt` or `alerting` profile is off. Stopping any of these containers does not affect the API,
the data plane or devices.

Retention:

| Variable | Default |
| --- | --- |
| `PROMETHEUS_RETENTION_TIME`, `PROMETHEUS_RETENTION_SIZE` | `15d`, `5GB` (whichever is hit first) |
| `LOKI_RETENTION_PERIOD` | `168h` |

Dashboards (folder "MeteorCloud", read-only in the UI): **API**, **Fleet**, **Host** and
**Logs**. The logs dashboard filters by service, level and `request_id`, and lists slow
requests from the access log.

## Enable it

### Local development

```bash
make dev OBS=1                              # app stack plus all components
make observability                          # same as make dev OBS=1
make observability OBS_PROFILES=metrics     # Prometheus only
```

Grafana is on http://localhost:3001 (`GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`; the
Grafana container refuses to start without a password, other profiles do not need one), Prometheus on http://localhost:9090. Plain `make dev` does not start this
stack; add `OBS=1`. `make stop` stops it either way. Profiles already in `COMPOSE_PROFILES` in `.env` (for example `mqtt`) are kept.

`make observability-check` validates the Prometheus, Loki, Alloy and Alertmanager configs and
runs the alert-rule unit tests (`rules/tests/alerts_test.yml`) with promtool.

### Installer (`installation.yaml`)

```yaml
observability:
  enabled: true
  backend: prometheus
  components:
    metrics: true      # Prometheus + node_exporter
    logs: true         # Loki + Alloy
    alerting: true     # Alertmanager (needs metrics)
    dashboards: true   # Grafana
  log_retention_hours: 168
  metrics_retention_days: 15
  metrics_retention_size: 5GB
```

Put `EDGE_PLATFORM_GRAFANA_ADMIN_PASSWORD` (required only with `dashboards`; `validate`
checks it) and, for alert delivery, `EDGE_PLATFORM_ALERT_WEBHOOK_URL` (https) in the
environment, not in the file. Add `EDGE_PLATFORM_ALERT_HEARTBEAT_URL` (https, needs the
webhook) to also get a dead-man's-switch heartbeat; see [Watchdog](#watchdog). `make aws-up` sizes the instance per enabled component and
the root disk from `metrics_retention_size`; Terraform opens no extra ports, because every
UI stays on 127.0.0.1. See [installer-configuration.md](installer-configuration.md).

### Ansible

| Variable | Default |
| --- | --- |
| `observability_enabled` | `false` |
| `observability_components` | `metrics,logs,alerting,dashboards` |
| `observability_log_retention` | `168h` |
| `observability_metrics_retention_time`, `observability_metrics_retention_size` | `15d`, `5GB` |
| `observability_alert_webhook_url` | `EDGE_PLATFORM_ALERT_WEBHOOK_URL` from the environment |
| `observability_alert_heartbeat_url` | `EDGE_PLATFORM_ALERT_HEARTBEAT_URL` from the environment (needs the webhook) |
| `observability_prometheus_url`, `observability_loki_url`, `observability_loki_push_url` | empty (bundled) |
| `metrics_enabled`, `access_log` | `true`, empty (follows `LOG_FORMAT`) |

The webhook URL is written to `<platform_root>/secrets/alertmanager/webhook_url` (mode 0600)
and Alertmanager reads it from there. Without it, alerts are visible in Alertmanager only.
With a heartbeat URL as well, it is written next to it (`heartbeat_url`) and Alertmanager
uses `alertmanager.webhook-heartbeat.yml`, which sends the Watchdog there every 5 minutes.

### Reaching the UIs

Every UI binds to 127.0.0.1 on the host. Use an SSH tunnel:

```bash
ssh -L 3001:127.0.0.1:3001 -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 user@platform-host
```

### Kubernetes (Helm)

The chart does not install Prometheus, Grafana or Loki.

```yaml
metrics:
  enabled: true            # METRICS_ENABLED on the API and data plane
  serviceMonitor:
    enabled: true          # needs the Prometheus Operator CRDs
    labels: { release: kube-prometheus-stack }
  prometheusRule:
    enabled: true          # the same rules as Compose
    labels: { release: kube-prometheus-stack }
  networkPolicyFrom:       # with networkPolicy.enabled; these peers can also reach the API
    - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: monitoring } }
      podSelector: { matchLabels: { app.kubernetes.io/name: prometheus } }
```

`values-monitoring.yaml` is a ready overlay. The ServiceMonitors set `job` to `backend` and
`data-plane`, so the rules and dashboards work unchanged. Logs go to stdout as JSON; collect
them with your cluster's log agent.

On the local k3d cluster, `make k8s-deploy OBS=1` installs kube-prometheus-stack
(`deploy/kubernetes/k3d-monitoring.yaml`, namespace `monitoring`) and deploys MeteorCloud with
`values-monitoring.yaml`. It needs `GRAFANA_ADMIN_PASSWORD`; reach the UIs with
`kubectl port-forward` (the command prints them). This is for local testing only: on a real
cluster, use the Prometheus your cluster already runs. No Loki in k3d.

### Summary

| Setup | Turn it on |
| --- | --- |
| Laptop (Compose) | `make dev OBS=1` |
| Server, Compose by hand | add `-f docker-compose.observability.yml` and the profiles ([Compose README](../deploy/compose/README.md)) |
| Server, Ansible / installer | `observability_enabled: true` / `observability.enabled: true` |
| Kubernetes (Helm) | `-f values-monitoring.yaml` on a cluster with the Prometheus Operator |
| Laptop (k3d) | `make k8s-deploy OBS=1` |

## CloudWatch

`backend: cloudwatch` with `enabled: true` is rejected at installer validate until a
CloudWatch agent is wired. It would be a new metrics adapter plus a log shipper; nothing in
the services changes.

## Runbooks

Each alert's `runbook` annotation links to its section here.

### APIHighErrorRate

More than 1% of public API requests returned 5xx for 2 minutes (only with at least 0.2 req/s).

1. Logs dashboard, service `backend`, level `error`: find the failing route and its `request_id`.
2. API dashboard, "Top 5xx routes", to see whether one route or everything is failing.
3. Everything failing usually means the database: check the `postgres` container and the
   API's connection errors. One route usually means a bug or a dependency (object storage, MQTT).
4. If a release just went out, roll back the image tag.

### APIErrorBurst

More than 10 public 5xx in 5 minutes, regardless of traffic. Same steps as
[APIHighErrorRate](#apihigherrorrate); at low traffic this is often one client retrying a
broken request.

### APIHighLatency

Public API p95 above 2 seconds for 10 minutes (downloads and uploads excluded).

1. API dashboard, "Slowest routes".
2. Logs dashboard, "Slow requests", for the matching `request_id`s.
3. Check host CPU and memory (Host dashboard) and database load. Fleet-wide list endpoints
   slow down with fleet size; check whether one organisation is polling heavily.

### ControlPlaneDown

Prometheus cannot scrape the API for 1 minute.

1. `docker compose ps backend` (or `kubectl get pods`), then its logs.
2. If the container is up, check `METRICS_ENABLED`: `false` also makes `/metrics` return 404,
   which looks like this alert. Turn the alert off or the metrics back on.
3. Check that Prometheus and the API share a network.

### DatabaseDown

The API answers scrapes but its fleet query has failed for 2 minutes, so logins, heartbeats
and Deploys are failing too (API error alerts are inhibited while this fires).

1. `docker compose ps postgres` (or the managed database's status page), then its logs.
2. Check the host disk ([HostDiskFull](#hostdiskfull)): a full disk stops Postgres.
3. Check `DATABASE_URL` and the connection limit (`too many connections` in the API logs).

This alert needs `FLEET_METRICS_ENABLED=true`; with fleet metrics off the gauge is absent and
the alert stays quiet.

### UnhandledExceptions

More than 5 requests crashed with an unhandled exception in 10 minutes. A crash is a bug,
unlike a deliberate 4xx/5xx. Logs dashboard, service `backend`, message `unhandled error`:
the stack trace and `request_id` are in the line. If a release just went out, roll back the
image tag.

### DataPlaneDown

The data plane is running (Prometheus found it) but its `/metrics` does not answer for
2 minutes. Devices keep their MQTT sessions with the broker, but telemetry is not ingested.
Check the `data-plane` container logs and restart it.

### DataPlaneMqttDisconnected

The data plane has no connection to the broker for 2 minutes. Check the `emqx` container, the
broker certificate (`MQTT_CA_CERT_PATH`) and `MQTT_PLATFORM_PASSWORD`. Data-plane logs show
the connect error.

### MqttIngestFailing

The data plane cannot forward MQTT messages to the API (non-ok results above 0.1/s for
5 minutes). Check that the API is up, that `MQTT_INTERNAL_TOKEN` matches on both sides, and
the API's `/internal/mqtt` errors in the logs.

### FleetOfflineSpike

More than 30% of seen devices are offline and more than 5 dropped off in 10 minutes. A single
cause is likely: the broker, the public MQTT address or certificate, DNS, or a network
outage at a site. Check [DataPlaneMqttDisconnected](#dataplanemqttdisconnected) first, then
whether the offline devices share an organisation or location. Offline means no heartbeat
for `DEVICE_OFFLINE_THRESHOLD_SECONDS`.

### DeploymentFailures

A Deploy ended as failed in the last hour. Open it in the console; per-device results show
which devices failed and why. Many failures across devices usually mean a bad artifact or
image; one device usually means that device.

### DeployPaused

A Deploy has been paused for 30 minutes. A phased Deploy pauses itself when its failure rate
crosses the threshold; an operator can also pause one. Open it in the console: the pause
reason and per-device results are shown there. Fix the release and resume, or cancel it or
roll back. Paused Deploys hold their devices, so new Deploys to those devices wait.

### DeploySweeperFailing

The background sweeper that advances batched and phased rollouts (delays, soak times,
pending timeouts) failed more than 3 Deploy ticks in 15 minutes. Rollouts then only move when
a device reports. Logs dashboard, service `backend`, message `Deploy tick failed`: the stack
trace names the Deploy. A database problem usually comes with
[DatabaseDown](#databasedown).

### RateLimitSpike

Requests are being rate-limited at more than 1/s for 10 minutes. The Fleet dashboard shows
which limiter. Usually a misconfigured agent loop or an enrolment script retrying; the
access log shows the route. Raise the limits only if the traffic is legitimate.

### LoginFailureSpike

More than 20 console logins failed in 10 minutes. Usually someone guessing passwords, or a
script with an old password. The access log shows the source route but never the IP; check
the Traefik access log or the firewall for the client address, and block it there. Make sure
admin accounts use strong passwords.

### MqttAuthDenySpike

The broker denied more than 0.5 client decisions per second for 10 minutes. Usually devices
that were deleted or disabled and keep reconnecting, or a client using an old certificate or
password. If no device was removed recently, treat it as probing: check the EMQX client list
and restrict port 8883 at the firewall if needed.

### InvalidRegistrationTokens

More than 10 device registrations used an invalid, expired or revoked token in 15 minutes.
Either an install script uses a token that was revoked or ran out of uses, or someone is
guessing tokens. Check recent token changes in the console; if none, revoke the active
registration tokens and issue new ones.

### HostDiskFull

A filesystem is over 90% full for 10 minutes. Usual suspects: Docker images
(`docker image prune`), artifact storage, Postgres, Prometheus or Loki data. Lower
`PROMETHEUS_RETENTION_*` or `LOKI_RETENTION_PERIOD` if monitoring data is the cause.

### HostDiskFillingUp

At the trend of the last 6 hours, a filesystem fills within 24 hours. Same causes as
[HostDiskFull](#hostdiskfull); act before it fires.

### Watchdog

Always firing, on purpose. If your receiver stops getting it, the alert pipeline (Prometheus
→ Alertmanager → webhook) is broken, or the whole host is down. Alertmanager runs on the same
host as the platform, so only an outside service can tell you that.

Set `EDGE_PLATFORM_ALERT_HEARTBEAT_URL` to a dead-man's-switch check (any service that
expects an HTTP POST every few minutes and alerts you when it stops). Alertmanager then sends
the Watchdog there every 5 minutes instead of to a null receiver. Set the check's grace
period to 10–15 minutes. Without a heartbeat URL, nothing tells you when the host itself is
down.
