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
`immediate`, `batched` (`batch_size`, optional `delay_seconds`) or `phased`
(canary and percentage phases with a soak time and a failure budget, see
[Rollout strategies](#rollout-strategies)). Use **Retry failed** and
**Rollback** on the Deploy detail page when needed. A
future **mender** provider would create the equivalent deployment in Mender and
store Mender’s id as `provider_ref`, while operators still use the same Releases
and Deployments UI/API.

Agents download release artifacts with their device token
(`GET /api/v1/agent/artifacts/{id}/download`) only when that artifact is on the
device’s current desired software. The agent verifies `X-Checksum-SHA256`.

## API

Requires `deployment.read` to list and view. Creating requires `deployment.create`.
Cancel and pause require `deployment.cancel`; resume requires `deployment.create`.
Device-group scoped operators can only target devices in their groups.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/deployments` | Filters: `status`, `release_id`, `page`, `page_size` |
| `POST` | `/api/v1/organizations/{org}/deployments` | Start a Deploy |
| `GET` | `/api/v1/organizations/{org}/deployments/{id}` | Detail + per-device rows |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/cancel` | Cancel an in-progress or paused Deploy |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/pause` | Stop activating new devices (batched/phased, in-progress only; `409` otherwise) |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/resume` | Resume a paused Deploy and tick it at once (`409` unless paused) |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/retry-failed` | Re-queue failed devices (`deployment.create`) |
| `POST` | `/api/v1/organizations/{org}/deployments/{id}/rollback` | New Deploy of the previous release (`deployment.rollback`) |

### Create body

```json
{
  "release_id": "<uuid>",
  "name": "optional label",
  "target": {
    "device_ids": ["<device-uuid>"],
    "group_ids": [],
    "device_type_ids": []
  },
  "rollout": { "strategy": "immediate" },
  "endpoint": {
    "hostname": "shop.meteor-edge.com",
    "port": 80
  }
}
```

`endpoint` is optional and only valid when the Deploy targets **one** device.
Each app/port needs its own hostname (80 and 8080 on the same device are two
endpoints). The public URL stays `unknown` until that workload is healthy.
See [Endpoints](endpoints.md).

Batched example: `"rollout": { "strategy": "batched", "batch_size": 2 }`.
Devices beyond the batch stay **queued** until the current batch is terminal.
Phased example: see [Phased body](#phased-body).

At least one of `device_ids`, `group_ids`, or `device_type_ids` is required.
Disabled devices are skipped. Empty expansion after filters returns `422`.

Responses include `provider` (e.g. `native`) and optional `provider_ref` (vendor id).

### Cancel

Works on in-progress and paused Deploys. Pending, updating, queued and timed-out
devices are marked cancelled. With the native provider, desired software on
activated devices (pending, updating, timed out) is restored to the previous
release when one was recorded; otherwise desired is cleared. A device whose
desired software a newer Deploy has since changed is left alone. Queued devices
never received the release and keep what they had. Devices that already succeeded stay
as they are.

## Rollout strategies

### Which one to use

| Fleet size | Suggested rollout |
|------------|-------------------|
| 1–10 devices | `immediate`, or `phased` with a canary of 1 (`Canary 1, then all`) |
| 10–100 devices | `phased` `1 / 10% / 100%` |
| 100+ devices | `phased` with `max_concurrent` and `failure_threshold_percent` |

`batched` remains for fixed-size waves without a failure budget.

### Phased body

```json
{
  "strategy": "phased",
  "phases": [
    { "count": 1 },
    { "percent": 10, "delay_seconds": 3600 },
    { "percent": 100, "delay_seconds": 86400 }
  ],
  "max_concurrent": 50,
  "failure_threshold_percent": 10,
  "pending_timeout_seconds": 86400
}
```

| Field | Limits | Meaning |
|-------|--------|---------|
| `phases` | 1–10 items, required | Cumulative share of the deployable (non-skipped) targets at the end of each phase |
| `phases[].count` | ≥ 1 | Absolute device count. Exactly one of `count` / `percent` per phase |
| `phases[].percent` | 1–100 | Share of deployable targets, rounded up, at least 1. The last phase must be `percent: 100` |
| `phases[].delay_seconds` | 0–604800 (7 days), not on phase 1 | Soak time, counted from when the previous phase became terminal |
| `max_concurrent` | 1–10000, default none | At most this many devices pending or updating at once within a phase |
| `failure_threshold_percent` | 0–100, default none | Pause when the failure rate reaches this value (`0` pauses on the first failure) |
| `pending_timeout_seconds` | 60–2592000 (30 days), default none | Mark a device `timed_out` when it stays pending this long after activation |

Sizes must be strictly increasing among `count` phases and among `percent`
phases. `batch_size` and `delay_seconds` are rejected for `phased`; the phased
fields are rejected for `immediate` and `batched`.

### How phases advance

Each device is **queued** until the rollout activates it: desired software is
written, the row becomes **pending** and `activated_at` is set. Within a phase
the most recently seen devices (`last_seen_at`) go first; batched keeps target
order.

The next phase starts when:

1. every activated device is terminal (`succeeded`, `failed`, `timed_out` or
   `cancelled`),
2. the next phase's `delay_seconds` has passed since that moment, and
3. the failure rate is below `failure_threshold_percent`.

Phase sizes are computed from the real fleet size. A phase that adds no
devices (for example 10% of 7 devices is still 1, the same as the canary) is
skipped together with its delay. With `max_concurrent`, a phase fills up as
earlier devices finish.

**Failure rate** is `failed / (succeeded + failed)` over all activated devices.
It uses current health: a device that installed and later reports
**unhealthy** (stopped or crashing) counts as failed, even if its install row
still says succeeded. For phased Deploys, an install that reports in-sync but
unhealthy is recorded as **failed**. Skipped, cancelled and timed-out devices
are not counted. The rate is checked on every tick, so a phase can pause half
way. Once every device is activated there is nothing left to hold back, so the
threshold no longer pauses the Deploy.

Rollouts advance on every heartbeat from a device in the Deploy and on a
background sweep every `DEPLOY_TICK_INTERVAL_SECONDS` (default `60`; `0`
disables the sweep, then only heartbeats advance rollouts). Each tick takes a
row lock on the Deploy, so several API replicas never activate the same phase
twice. One replica sweeps at a time (a Postgres advisory lock), and each Deploy
is committed on its own, so a heartbeat waits for at most one Deploy's tick.

The Deploy detail page shows **Phase N of M** and the soak time left before
the next phase.

### Offline devices

An activated device that is offline stays **pending** (the console shows
**Waiting for device** until it checks in). With `pending_timeout_seconds`
set, it becomes **timed_out** that long after activation, which counts as
terminal so the phase can move on, and is excluded from the failure rate.
Desired software is left in place: when the device comes back it still
installs the release, and its row moves to succeeded or failed. A late report
never reopens a Deploy that already finished. A Deploy whose devices all
timed out (or failed) ends as **failed**.

### Pause, resume, cancel, rollback

- **Pause** (`deployment.cancel`) stops new activations on an in-progress
  batched or phased Deploy. Activated devices keep installing and reporting.
  `rollout.state.paused_reason` is `manual`, or `failure_threshold` when the
  failure budget paused it; `paused_at` records when.
- **Resume** (`deployment.create`) clears the pause and ticks at once. The
  failure rate is checked again, so a Deploy paused by the threshold pauses
  again unless the rate dropped: use **Retry failed** first (it keeps the
  Deploy paused), or cancel / roll back.
- **Cancel** works while paused (see [Cancel](#cancel)).
- **Rollback** works on paused and finished Deploys and only targets devices
  that were activated (received the release). Queued devices are left alone.
  Once a Deploy is rolled back, **Resume** and **Retry failed** on it return
  `409 deployment_rolled_back`, so they cannot write the release again.

### Compared with Balena and Mender

We follow **Mender**'s model: phased, pull-based Deploys. Devices fetch
desired software on their own schedule, a Deploy rolls out to the fleet in
phases, and offline devices pick up the release when they return. Where Mender
schedules phases by start time, MeteorCloud phases advance on results: every
activated device finished, the soak delay passed and the failure rate is under
budget. **Balena** moves a whole fleet to a release (with per-device pinning)
rather than running phased campaigns. Image-based A/B partition OTA (Mender's
root filesystem updates, balenaOS host updates) is out of scope here:
MeteorCloud Deploys update systemd services, containers and compose
applications.

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

**Deployments** in the organization menu: list, detail with Pause / Resume /
Cancel, and a Deploy wizard (release → devices/groups → review). The review
step picks **Immediate**, **Batched** or **Phased** (presets
`Canary 1, then all` and `1 / 10% / 100%`, with an Advanced section for the
failure threshold, max concurrent and pending timeout). **Advanced YAML**
starts from the wizard's choices; once you edit the YAML it is kept until you
press **Fill from wizard**. You can also start a Deploy from a release
row or from an empty device Software panel.

## After you deploy

The Deploy detail page shows three things per device, and they are separate:

- **Install** — did the agent install the release? A Deploy is **Succeeded**
  once every device reports the release in-sync. It stays Succeeded even if
  the process stops later.
- **Health** — is it running now? The agent checks on every heartbeat
  (systemd unit active; container or every compose service running and
  passing its Docker `HEALTHCHECK`). `Unknown` while a `HEALTHCHECK` is
  still starting or before the first report.
- **URL** — only when this Deploy created an endpoint. `ready` needs install
  in-sync, health healthy and the WireGuard tunnel up.

The legend sums these up, for example `8/10 installed · 7 healthy · 1 URL ready`.
The page refreshes every 5 s while the Deploy runs or is paused and every 30 s after.

**Retry failed** is for install failures only. If the software crashes after a
successful install, the agent does not reinstall it (unless the container or
compose project was removed entirely): the device shows
**Unhealthy** with a short error. Open the device page and use **View logs**
(or the Logs window) to see why it stopped.

## Related

- [Releases](releases.md)
- [Heartbeat](heartbeat.md)
- [Agent README](../../src/device-plane/agent/README.md) (Raspberry Pi OS install)
- [RBAC](../authorization/rbac.md)
