# Deployments

A **Deploy** sends a [release](releases.md) to selected devices (or groups / types).
MeteorCloud always records the Deploy (history + per-device progress) and keeps
the release catalog. Who **executes** the Deploy is selected with
`DEPLOYMENT_PROVIDER`.

With the default **native** provider, MeteorCloud writes **desired** software on
each target and agents apply it (`systemd`, `docker`, `docker_compose` — see
[Releases](releases.md)). Optional MQTT `sync_desired` wakes them to poll sooner.
Devices that reported they lack the release runtime (e.g.
`capabilities.systemd: false`) are marked **skipped**. Rollout may be
`immediate` or `batched` (`batch_size`, optional `delay_seconds`). Use
**Retry failed** and **Rollback** on the Deploy detail page when needed. A
future **mender** provider would create the equivalent deployment in Mender and
store Mender’s id as `provider_ref`, while operators still use the same Releases
and Deployments UI/API.

Agents download release artifacts with their device token
(`GET /api/v1/agent/artifacts/{id}/download`) only when that artifact is on the
device’s current desired software. The agent verifies `X-Checksum-SHA256`.

## API

Requires `deployment.read` to list and view. Creating requires `deployment.create`.
Cancel requires `deployment.cancel`. Device-group scoped operators can only target
devices in their groups.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/deployments` | Filters: `status`, `release_id`, `page`, `page_size` |
| `POST` | `/api/v1/organizations/{org}/deployments` | Start a Deploy |
| `GET` | `/api/v1/organizations/{org}/deployments/{id}` | Detail + per-device rows |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/cancel` | Cancel in-progress only |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/retry-failed` | Re-queue failed devices (`deployment.create`) |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/rollback` | New Deploy of the previous release (`deployment.rollback`) |

### Create body

```json
{
  "release_id": "<uuid>",
  "name": "optional label",
  "target": {
    "device_ids": [],
    "group_ids": [],
    "device_type_ids": []
  },
  "rollout": { "strategy": "immediate" }
}
```

Batched example: `"rollout": { "strategy": "batched", "batch_size": 2 }`.
Devices beyond the batch stay **queued** until the current batch is terminal.

At least one of `device_ids`, `group_ids`, or `device_type_ids` is required.
Disabled devices are skipped. Empty expansion after filters returns `422`.

Responses include `provider` (e.g. `native`) and optional `provider_ref` (vendor id).

### Cancel

Pending / updating devices are marked cancelled. With the native provider, desired
software is restored to the previous release when one was recorded; otherwise
desired is cleared. Devices that already succeeded stay as they are.

## Provider

| Value | Meaning |
|-------|---------|
| `native` (default) | MeteorCloud writes desired software; agents download/apply |
| `none` | Deploy routes return 404 |
| `mender` | Stub: Deploy execute/cancel return 501 until a Mender adapter exists. Releases and Deploy history/UI still work when you implement the adapter — same APIs, execution goes to Mender |

The port is `DeploymentProvider` in `app/ports/deployment.py`. Adapters live under
`app/adapters/` (`native_deployment`, `mender_deployment`). Domain services must
not import Mender (or other vendor) clients directly.

## UI

**Deployments** in the organization menu: list, detail with Cancel, and a Deploy
wizard (release → devices/groups → review). You can also start a Deploy from a
release row or from an empty device Software panel.

## Related

- [Releases](releases.md)
- [Heartbeat](heartbeat.md)
- [Agent README](../../src/device-plane/agent/README.md) (Raspberry Pi OS install)
- [RBAC](../authorization/rbac.md)
