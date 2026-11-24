# Software on a device

Open a device, then **Software**, then a workload. The page shows one workload that MeteorCloud
deployed to that device. Containers and services you run yourself are not shown and cannot be touched.

| Tab | What you see |
| --- | --- |
| Overview | Containers or Compose services, or the systemd unit: state, restarts, image, ports, mount paths, networks, volumes. Never environment variables or commands |
| Health | Healthy, unhealthy, stopped or unknown over 24 hours, 7 or 30 days, with deploys, lifecycle events and CPU/memory per container (the first 8). Needs the [data plane](../data-plane/platform-history.md) |
| Logs | The [device logs](logs.md) of this workload only |
| Activity | Who ordered what, and how it ended |

**Seen … ago** tells how old the picture is. The device sends it when something changed and every 5
minutes; **Refresh now** asks for it at once.

## Restart, stop and start

- A Compose workload can be restarted, stopped or started as a whole or one service at a time. After
  a stop of the whole workload, start the whole workload.
- **Stop** stays stopped, also after a reboot, until **Start**, **Restart** or a new deploy. A stopped
  workload is not a failed one: rollouts ignore it.
- The device gets the order over MQTT at once, or with its next heartbeat without MQTT. An order it does
  not collect in time expires.
- While a deploy runs, the device refuses. Order it again afterwards.
- The same order twice while the first waits is one order. A device takes at most 10 orders a minute
  (`429 too_many_software_actions`).

Software deployed before the device had this version of meteorcli shows its state, but has no buttons
until it is deployed again: the device changes only what it can prove MeteorCloud deployed.

A container with the restart policy `always` starts when the Docker daemon restarts. The agent stops it
again within its next cycle.

## Who may do what

| Action | Permission | Roles |
| --- | --- | --- |
| See the page, refresh | `device.read` | all |
| Restart, stop, start | `software.manage` | owner, admin, operator |
| Logs | `device.logs` | owner, admin, operator, developer |
| See who ordered an action | `member.read` | owner, admin |

Every restart, stop and start is in the [audit log](../audit-log.md).

## API

Under `/api/v1/organizations/{org}/devices/{device}/software/{software}`:

| Route | |
| --- | --- |
| `GET` | The workload, its latest snapshot, `supported_actions`, `can_manage` |
| `GET /history?range=24h\|7d\|30d` | Health segments, events, deploys, series; `503` without the data plane |
| `GET /actions` | The last 50 actions |
| `POST /actions` | `{"kind": "workload_restart", "service": "web"}`; kinds `workload_restart`, `workload_stop`, `workload_start`, `workload_snapshot` |
