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

The Overview lists containers like `docker ps`: name, image, status with uptime, created, ports, plus CPU,
memory, processes and the disk space the container wrote in its own filesystem (not its logs or volumes).
The command is left out, as it can hold secrets.

For containers the Overview also shows what they use: CPU (100% is one core), memory against its limit,
network and disk traffic since the container started, and processes; volume sizes; network subnets; and
Docker's storage on the device with how much of it is reclaimable as Docker counts it. CPU and the like are from the moment
the device sent the picture. Docker's storage, volume and container sizes are measured every 30 minutes, and again
by **Refresh now**, because measuring reads every file in the volumes. The storage numbers are totals of the whole device; names of other software are never sent. Nothing is cleaned up from here.

### Memory says "Not measured"

The kernel of the device does not count memory per container. Raspberry Pi OS is like that by default
(`cgroup_disable=memory`); Docker cannot see it there either. Memory limits do not work without it.

1. On the device, check. Without `memory` in the output it is off:

   ```bash
   cat /sys/fs/cgroup/cgroup.controllers
   ```

2. Add `cgroup_enable=memory` to the end of the one line in `/boot/firmware/cmdline.txt` (older images:
   `/boot/cmdline.txt`). The file must stay one line. Then reboot:

   ```bash
   sudo cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak
   sudo sed -i '1 s/$/ cgroup_enable=memory/' /boot/firmware/cmdline.txt
   sudo reboot
   ```

3. Check again: the first command lists `memory` (`/proc/cmdline` may still show `cgroup_disable=memory`
   too; the later option wins), and `docker stats --no-stream` shows a value.
4. Open the page: the value comes with the next picture (up to 5 minutes), or use **Refresh now**.

The reboot stops the containers. One with the restart policy `no` (the default) does not come back by
itself: use **Start** on its page, or set `restart: unless-stopped` in the Compose file.

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
