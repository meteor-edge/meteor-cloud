# Device Wi-Fi from the console

An owner or admin can add a Wi-Fi network to a device, change the password of one
it has, or remove one, from **Devices → a device → Settings → Wi-Fi**. The device
does not have to be touched and does not need the VPN or SSH for this.

Requirements on the device: meteorcli 0.3.0 or newer installed with `sudo`
(this installs the `meteorcli-wifi` helper), NetworkManager, and a Wi-Fi adapter.
Raspberry Pi OS Bookworm and newer, and Ubuntu Desktop, use NetworkManager. A device
without it reports "Wi-Fi cannot be managed" with the reason, and the console shows it.

WPA2/WPA3 personal (a password) and open networks are supported. Enterprise Wi-Fi
(802.1X: user name, certificates) is not; see [Not included](#not-included).

## Two ways to apply a change

| Choice | What the device does | Use it when |
|--------|----------------------|-------------|
| **Connect now and check** | Switches to the network. If it cannot join it, or cannot reach MeteorCloud through it within 90 seconds, it puts its old settings back and reconnects | The network is in range now: a new site network, or the router's password is already changed |
| **Save for later** | Stores the network and stays on its current connection. It is used the next time the device sees that network | **Before** you change the router's password, or for a network at another site. It is not tested |

To change a router's password without losing devices: send the new password with
**Save for later**, wait until every device shows it as changed, then change the router.
The old password stays wrong on the device afterwards, but the new profile wins.

**Remove** is refused by the device when the network is the one it is connected
through and no cable is plugged in, because that would cut it off.

## Which connection a device uses

**Devices → a device → Network → Connection** shows the link the device's traffic
uses now: Ethernet, Wi-Fi (with the network name and signal), a mobile network, or
another link, plus its interface (`eth0`, `wlan0`) and whether the other one is also
connected. It is the IPv4 default route with the lowest metric, so with a cable and
Wi-Fi both up it shows Ethernet. meteorcli 0.3.0 or newer reports it with every change
and at least every 5 minutes; it does not need NetworkManager or the Wi-Fi helper.

## How the password is protected

1. The console sends the password once over HTTPS. No response, list or audit entry
   ever contains it; the audit log records who changed which network on which device.
2. The server stores it encrypted (AES-256-GCM). The key is derived from
   `SECRETS_ENCRYPTION_KEY`, or from `APP_SECRET_KEY` when that is empty, and the
   ciphertext is bound to the device and the change, so it cannot be moved to another row.
3. The device's heartbeat response says a change is waiting (`wifi_pending`). The agent
   fetches it with its own device credential. Only that device can read it.
4. As soon as the device reports the outcome (changed, rolled back or failed) the server
   **erases its copy**. A password a device never collects is erased after
   `WIFI_PASSWORD_TTL_SECONDS` (default 7 days) and the change is marked failed.
5. On the device the agent passes the password to the root-owned helper on stdin (never
   on a command line, never in a log). The helper writes one NetworkManager profile,
   readable by root only.

Rotating `SECRETS_ENCRYPTION_KEY` (or `APP_SECRET_KEY` without it) makes waiting
passwords unreadable; those changes are marked failed and can be sent again.

Permission: `device.network` (owner and admin by default), for seeing a device's Wi-Fi and
connection as well as changing them. Network names and the link a device uses are
sensitive, so other roles do not see the Wi-Fi section or the Connection card, and
`GET …/wifi` answers `403`. Add `device.network` to a custom role to grant it.

## On the device

`/usr/local/libexec/meteorcli-wifi` is installed by `sudo ./installcli.sh` with a sudo
rule for that one file, like the other meteorcli helpers. It accepts a network name, a
password, "hidden" and "connect now", validates them, writes
`/etc/NetworkManager/system-connections/meteor-wifi-<hash>.nmconnection` and runs fixed
`nmcli` commands. It cannot run other commands or write other files.

For **Connect now and check** the helper keeps the replaced profiles in
`/var/lib/meteorcli-wifi/` until the agent confirms. If the device loses power in
between, the agent puts the old settings back when it starts again and reports that.

Networks written at image time (`meteor-wifi`, `preconfigured`) are listed too. Changing
the password of one replaces its profile. A network that the operating system's own
netplan configuration defines (Raspberry Pi Imager and cloud-init write
`/etc/netplan/50-cloud-init.yaml`) is removed from that YAML with `netplan set` and
the configuration regenerated, so it does not come back at boot. Without `netplan`
on the device such a network cannot be removed from the console.

**Remove** forgets the network and disconnects from it at once if it is in use. It is
refused while that network is the device's only connection: add another network or
plug in a cable first.

## API

Admin (bearer user token):

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/devices/{id}/wifi` | `status` (what the device last reported, or null) and `networks` (changes ordered here, with `state`: `pending`, `applied`, `rolled_back`, `failed`, and `last_error`) |
| `PUT` | `/api/v1/organizations/{org}/devices/{id}/wifi/networks` | `{ "ssid", "security": "wpa-psk" \| "open", "password", "hidden", "connect_now" }` |
| `POST` | `/api/v1/organizations/{org}/devices/{id}/wifi/networks/remove` | `{ "ssid" }` |

Agent (device credential):

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/agent/network/wifi` | Waiting changes, with the password |
| `POST` | `/api/v1/agent/network/wifi/report` | `status` and `results` (`id`, `revision`, `state`, `error`). A result for an older revision of a change is ignored. `status.uplink_type` (`ethernet`, `wifi`, `cellular`, `other`; unknown values are stored as `other`) and `status.uplink_interface` are optional |

## Not included

- Enterprise Wi-Fi (802.1X). It needs certificates or a user name per device and a
  different form; the file format and helper would grow a second profile type.
- Devices that manage Wi-Fi with `wpa_supplicant` alone or `systemd-networkd`.
- Static IP addresses, DNS and proxies.
