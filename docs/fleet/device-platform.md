# Device platform (Pi, NUC, Jetson, …)

One `meteorcli` binary runs on managed Linux devices. Device types in the console
are catalog rows (labels and defaults), not separate agents.

Board and OS differences live in the Go agent's
[`internal/platform`](../../src/device-plane/agent-go/internal/platform/) package (inventory,
metrics, tool probes) and in its root helper (host actions):

| Port | Common contract | Linux default |
|------|-----------------|---------------|
| Inventory | `serial_number`, `mac_addresses`, `hostname`, `os_*`, `kernel_version`, `architecture`, `hardware_model`, `cpu_model`, `cpu_cores`, `memory_mb`, `local_ip`, `agent_version` | `/proc`, device-tree (Pi/Jetson), DMI (NUC), interface IPv4; agent_version from meteorcli |
| Metrics | `cpu_percent`, `memory_percent`, `disk_percent`, optional `temperature_c` | `/proc`, `statvfs`, thermal zones |
| Capabilities | `systemd`, `docker`, `docker_compose`, `wireguard`, `ssh` | command probes |
| Host actions | `reboot`, `shutdown`, `set_password`, `add_user` | root helper (`meteorcli-helper.socket`) → `systemctl` / `useradd` |

Implementors for a new board or OS keep the field names and action kinds above (the control
plane and console depend on them) and extend `internal/platform` (board detection, collectors)
or the helper's operations ([Root helper](../meteorcli-go.md#root-helper)).

Wi-Fi assumes NetworkManager; first-boot packaging is Pi-image oriented.

Related: [Device actions](device-actions.md), [Device types](device-types.md),
[Heartbeat](heartbeat.md), [MQTT metrics](mqtt.md).
