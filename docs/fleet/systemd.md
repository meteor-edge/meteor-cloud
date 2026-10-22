# systemd deploys

MeteorCloud can install a **systemd** unit on Linux devices that run the agent.

## Flow

1. Upload a `systemd` [artifact](artifacts.md) (unit file).
2. Create a [release](releases.md) with kind `systemd`, that artifact, and
   `spec.unit_name` (optional `spec.start`, default true).
3. [Deploy](deployments.md) the release to devices.
4. On the next heartbeat the agent:
   - downloads the unit (`GET /api/v1/agent/artifacts/{id}/download`)
   - installs it under `/etc/systemd/system`, runs `daemon-reload`, `enable`,
     and `restart` when `start` is true
   - checks `systemctl is-active`
   - reports `installed_release_id` + `health=healthy` only when the unit is
     active (or when `start` is false)

## Capabilities

Heartbeats include `capabilities.systemd` (and docker flags). If a device has
reported `systemd: false`, a systemd Deploy marks that device **skipped** and
does not set desired software for it. Devices that have never reported
capabilities are still targeted (the agent fails the apply if systemd is
missing).

## Agent runtime

The adapter lives in [`src/device-plane/runtime/systemd.py`](../../src/device-plane/runtime/systemd.py)
(`apply`, `health_check`, `recover`). See also [Docker](docker.md) and
[Raspberry Pi OS](raspberry-pi.md).

## Related

- [Agent artifacts](agent-artifacts.md)
- [Heartbeat](heartbeat.md)
- [Deployments](deployments.md)
- [Docker](docker.md)
