# Device platform (Pi, NUC, Jetson, …)

One `meteorcli` binary runs on managed Linux devices. Device types in the console
are catalog rows (labels and defaults), not separate agents.

Board and OS differences go behind **platform ports** in
`src/device-plane/agent/edge_agent/platform/`:

| Port | Common contract | Linux default |
|------|-----------------|---------------|
| Inventory | `serial_number`, `mac_addresses`, `hostname`, `os_*`, `kernel_version`, `architecture`, `hardware_model`, `cpu_model`, `cpu_cores`, `memory_mb`, `local_ip`, `agent_version` | `/proc`, device-tree (Pi/Jetson), DMI (NUC), interface IPv4; agent_version from meteorcli |
| Metrics | `cpu_percent`, `memory_percent`, `disk_percent`, optional `temperature_c` | `/proc`, `statvfs`, thermal zones |
| Capabilities | `systemd`, `docker`, `docker_compose`, `wireguard`, `ssh` | command probes |
| Host actions | `reboot`, `shutdown`, `set_password`, `add_user` | `meteorcli-action` → `systemctl` / `useradd` |

Implementors for a new board or OS:

1. Keep the field names and action kinds above (control plane and console depend on them).
2. Add an adapter that implements the ports (start from `LinuxPlatform` /
   `SystemdHostActions`).
3. Select it from configuration later if needed; today the agent uses
   `default_platform()` (Linux + systemd).

### Small example (another OS)

NUC on Ubuntu: no new adapter — Linux default already fits.

A different OS only needs the same methods. Stub in the tree:
`edge_agent/platform/example_acme.py` (`AcmeHostActions`). Wire it when you
detect that OS (selection is still manual today):

```python
from edge_agent.actions import ActionSync
from edge_agent.platform.example_acme import AcmeHostActions

# Instead of the Linux helper:
ActionSync(client, device_token, host=AcmeHostActions())
```

Inventory/metrics stay the Linux collectors until that OS needs its own
`InventoryCollector` / `MetricsCollector` with the **same keys**.

Wi-Fi still assumes NetworkManager on the stock helper; first-boot packaging is
Pi-image oriented. Those can grow their own adapters the same way.

Related: [Device actions](device-actions.md), [Device types](device-types.md),
[Heartbeat](heartbeat.md), [MQTT metrics](mqtt.md).
