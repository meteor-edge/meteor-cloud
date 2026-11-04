# Device WiFi settings

Post-enroll WiFi for a device that already reaches the control plane. This is
**device network desired state**, not a software release or artifact.

First boot still uses Raspberry Pi Imager, cloud-init, or Ethernet. MeteorCloud
cannot push WiFi before the agent can talk to the API.

## Who should set WiFi?

| Situation | Who |
|-----------|-----|
| First boot / golden image | **Customer / imaging** (Raspberry Pi Imager: SSID + **WiFi country**) |
| Device already enrolled, move to a new site SSID | **MeteorCloud** device WiFi settings (this feature) |
| One-off lab Pi | Either; Imager is simpler |

Cloud WiFi is an operator convenience for **post-enroll** changes. It is not a
replacement for correct image setup (country / rfkill / first network).

## Operator flow

1. Enroll the device (any path that gets `meteorcli` online).
2. Open **Devices → device → Network → WiFi**.
3. Confirm **Connected interfaces** (ethernet / wifi from the last heartbeat).
4. Set SSID, PSK, optional interface (`wlan0`) and hidden flag. Save.
5. The agent fetches desired WiFi on heartbeat (or sooner if MQTT `sync_network`
   is delivered) and applies it with the `meteorcli-wifi` helper.

Prefer keeping Ethernet connected while changing WiFi so the agent can report
success or revert if the new network fails.

## API

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/devices/{id}/wifi` | Public fields + `psk_set`; **never** plaintext PSK |
| `PUT` | `/api/v1/organizations/{org}/devices/{id}/wifi` | Body: `ssid`, `psk`, optional `interface`, `hidden` (`device.update`) |
| `DELETE` | `/api/v1/organizations/{org}/devices/{id}/wifi` | Stop cloud-managing WiFi for this device |
| `GET` | `/api/v1/agent/network` | Device token; includes PSK for **this** device only |

PSK is encrypted at rest (`DEVICE_SECRETS_KEY` or `APP_SECRET_KEY`). Changing
settings bumps `revision`. Stale agent reports with an older revision are ignored.

## On the device

- Helper: `/usr/local/libexec/meteorcli-wifi` (installed by `sudo ./installcli.sh`)
- Uses NetworkManager (`nmcli`); upserts a connection named `meteorcloud-<ssid>`
- Does **not** delete other WiFi or Ethernet profiles
- On apply failure, attempts to reactivate the previous connection
- Capability flag: `wifi` on heartbeat when NM + a wifi device are present

Re-run the installer with `sudo` after upgrading so the helper and sudoers rule
are present.

## Security notes

- No WiFi artifact, release, Ansible, or remote shell
- Org APIs never return the plaintext PSK
- The helper is narrowly scoped; while `meteorcli.service` runs as root, a
  compromised agent can still bypass helpers — see residual risk in
  [architecture](../architecture.md)
- Maintainer risk does not apply here (no apt); lockout risk does — keep a
  fallback link when changing site WiFi

## Related

- [meteorcli](../meteorcli.md)
- [First-boot enrollment](../../src/device-plane/agent/packaging/firstboot/README.md)
- [Connectivity (WireGuard)](connectivity.md)
