# Releases and Deploy contracts (control ↔ device)

Plane boundary: control-plane owns the catalog and desired state; the device
agent applies kinds and reports installed software. Do not share Python modules
across planes — only these HTTPS/MQTT shapes.

## Desired software (agent)

`GET /api/v1/agent/software` and heartbeat `desired_software[]` items:

| Field | Meaning |
|-------|---------|
| `workload_key` | Stable id (unit / container / project name) |
| `kind` | `systemd` \| `docker` \| `docker_compose` |
| `release_id` | Desired release UUID |
| `release_version` | Version string |
| `artifact_id` | Optional download id |
| `spec` | Kind-specific install config |
| `desired_revision` | Monotonic revision for wake-ups |

## Software report (agent → control)

Heartbeat `software_report[]` (and MQTT `commands/result` type `software`):

| Field | Meaning |
|-------|---------|
| `workload_key` | Same key as desired |
| `installed_release_id` | Set only when healthy / in sync |
| `installed_version` | Optional |
| `sync_status` | `pending` \| `updating` \| `in_sync` \| `failed` \| `cancelled` |
| `progress_percent` | 0–100 while updating |
| `health` | `unknown` \| `healthy` \| `unhealthy` |
| `last_error` | Failure detail |

## Capabilities

Heartbeat `capabilities` object (booleans):

- `systemd` — systemctl + systemd runtime present
- `docker` — `docker` CLI present
- `docker_compose` — `docker compose version` succeeds

Deploy skips devices that reported the required capability as `false`.

## MQTT (optional)

- `devices/{id}/commands` — `{ "type": "sync_desired", "revision": n }`
- Agent then GETs desired over HTTPS; does not install from MQTT payloads alone.

## Kind specs (summary)

See [docs/fleet/releases.md](../docs/fleet/releases.md) for operator-facing
fields. Device adapters live under `src/device-plane/runtime/`.
