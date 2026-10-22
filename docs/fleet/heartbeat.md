# Heartbeat and connectivity status

Devices report liveness by sending periodic heartbeats. Connectivity status is
derived from the last heartbeat time.

## Endpoint

```
POST /api/v1/agent/heartbeat
Authorization: Bearer dev_...
```

Optional body:

```json
{
  "hostname": "edge-01",
  "os_version": "22.04",
  "kernel_version": "6.2.0",
  "metrics": {},
  "capabilities": { "systemd": true, "docker": true, "docker_compose": true },
  "software_report": [
    {
      "workload_key": "camera-agent.service",
      "installed_release_id": "…",
      "installed_version": "1.2.0",
      "health": "healthy"
    }
  ]
}
```

Response:

```json
{
  "device_id": "…",
  "status": "online",
  "heartbeat_interval_seconds": 60,
  "server_time": "2026-08-18T10:00:00Z",
  "desired_software": [
    {
      "workload_key": "camera-agent.service",
      "kind": "systemd",
      "release_id": "…",
      "release_name": "camera-agent",
      "release_version": "1.2.0",
      "artifact_id": "…",
      "revision": 1,
      "spec": { "unit_name": "camera-agent.service", "start": true }
    }
  ]
}
```

Each heartbeat updates the device's `last_seen_at` and may refresh a few
inventory fields. `software_report` updates installed software for known
workloads (unknown keys are ignored). The response includes current
`desired_software`. Agents can also poll `GET /api/v1/agent/software`.
When a desired item has an `artifact_id`, the agent downloads it from
`GET /api/v1/agent/artifacts/{id}/download` (only if that artifact is on current
desired software) and reports `updating` / `failed` as needed. See
[Deployments](deployments.md). Disabled or credential-revoked devices are
rejected.

## Connectivity status

Status is computed consistently everywhere (list, detail, filters) from
`last_seen_at`:

| Status | Meaning |
|--------|---------|
| `online` | last seen within the offline threshold |
| `offline` | last seen, but longer ago than the threshold |
| `never_seen` | has never sent a heartbeat |

Two settings control the timing (defaults shown):

- `DEVICE_HEARTBEAT_INTERVAL_SECONDS=60` — interval advertised to agents.
- `DEVICE_OFFLINE_THRESHOLD_SECONDS=150` — window before a device is offline.

The threshold is intentionally larger than the interval so a single missed
heartbeat does not immediately flip a device offline.

## Reference agent

```bash
meteorcli run          # loop using the advertised interval
meteorcli run --once   # single heartbeat
```

The loop uses bounded exponential backoff on transient failures and stops if the
credential is rejected so the device can be re-registered.
