# Automatic rollback on each device

A release can ask every device to check the update and go back to the release it ran
before if the update is not healthy. The device does it on its own, within a minute, without
waiting for an operator. This complements the operator's **Rollback** on a Deploy
([deployments](deployments.md#pause-resume-cancel-rollback)), which moves a whole group of
devices back.

## Turn it on

Add `health_check` to the release spec (any kind), or tick **Roll back automatically if the
update is not healthy** when you create the release in the console.

```json
{
  "name": "web",
  "version": "2.0.0",
  "kind": "docker",
  "spec": {
    "container_name": "web",
    "image": "ghcr.io/acme/web:2.0.0",
    "health_check": {
      "timeout_seconds": 60,
      "stable_seconds": 10,
      "http_url": "http://127.0.0.1:8080/health",
      "rollback": true
    }
  }
}
```

| Field | Default | Meaning |
|-------|---------|---------|
| `timeout_seconds` | `60` (5–1800) | How long the new release has to become healthy |
| `stable_seconds` | `10` (0–600) | How long it must then stay healthy without a break |
| `http_url` | none | Optional `http://` or `https://` URL the device calls; 2xx or 3xx counts as healthy. Redirects are not followed |
| `rollback` | `true` | `false` only waits and reports `failed`, without going back |

Healthy means the runtime says so **and** `http_url` (if set) answers:

- **systemd:** the unit is active.
- **docker:** the container is running, not restarting, and its Docker `HEALTHCHECK` (if the
  image has one) passes.
- **docker_compose:** every service container is running and healthy.

## What the device does

1. It applies the new release as usual.
2. It watches the workload for up to `timeout_seconds` + `stable_seconds`, every 2 seconds. A
   workload that turns unhealthy starts the stable window over.
3. If the release fails to apply, or is not healthy in time, the device applies the release it
   ran before, from what it still has locally (the cached artifact, the pulled image).
4. It reports `sync_status: rolled_back`, with the previous release as installed and the
   reason in `last_error`, for example `Rolled back to 1.4.0: Health check failed: container
   web HEALTHCHECK failed`.
5. It does not try the failed release again. A new Deploy (a new desired revision, for example
   a fixed release) is tried again.

If there is no previous release (the first install), or going back fails too, the device
reports `failed` with both reasons, as before.

## In the Deploy

A device that rolled back counts as **failed** for its Deploy, so a phased rollout with
`failure_threshold_percent` pauses on its own when enough devices roll back
([phased rollouts](deployments.md#phased-body)). The device's software panel shows
**Rolled back** and the reason.

## Limits

- The previous release must still be on the device. Artifacts are kept in the agent's cache;
  a docker image that was removed by hand cannot be restored.
- Releases without `health_check` keep today's behaviour: the agent checks once right after
  applying and reports `failed` if the workload is not running.
- Workloads bound to a MeteorCloud application keep their credential during a rollback.
