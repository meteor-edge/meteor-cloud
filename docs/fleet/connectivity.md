# Device connectivity (WireGuard) and browser SSH

MeteorCloud reaches devices on a **private WireGuard network**. Devices dial out
to the gateway (NAT-friendly). SSH is never published on the public internet.
Public app URLs are separate; see [Endpoints](endpoints.md).

Ansible sets up the WireGuard server (the **gateway**) on the API host, or on
its own server on AWS or on-prem. The control plane can run anywhere: Compose,
another cloud, or Kubernetes (Helm), which uses a dedicated gateway outside the
cluster.

## What is stored where

| Secret | Where |
| --- | --- |
| Device WireGuard **private** key | Device only (`/etc/meteorcli/wireguard/private.key`) |
| Device WireGuard **public** key | Device + control plane |
| Server WireGuard **private** key | Gateway only (`/etc/wireguard/server.key`, generated there, or `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY`) |
| SSH gateway private key | Cloud host (`SHELL_SSH_IDENTITY_FILE`) |
| Browser | Short-lived `sh_…` ticket only |

If an SD card dies, revoke the old peer and **re-enroll** (new device id). A
revoked device cannot register WireGuard again. The control plane cannot restore
the lost private key.

## Set up the WireGuard server (Ansible)

Ansible installs everything on Ubuntu 22.04+, Debian 12+, or RHEL/CentOS/Rocky/AlmaLinux 9+:
`wireguard`, `wireguard-tools`, `iptables`, the kernel module, and persistent
IPv4 forwarding. It generates the server key **on the gateway** once
(`/etc/wireguard/server.key`; `wg0.conf` loads it in `PostUp` and never contains
it) and starts `wg-quick@wg0` plus the **peer gateway agent**
(`meteorcloud-gateway.service`). The agent applies `wg set` from the control
plane every 15s and never rewrites `wg0.conf`; after a `wg-quick` restart it
re-adds peers on the next poll. Open **UDP 51820** to the gateway (security
group, or a router port forward on-prem).

### The address devices dial: `vpn.<domain>`

One of the [DNS records](../dns.md) MeteorCloud needs.

Give the gateway a DNS name, e.g. `vpn.fleet.example.com` (`wg.<domain>` also works), as an **A record to its
public IP** (DNS only: no HTTP proxy such as Cloudflare's orange cloud, since
WireGuard is UDP). Set it on the gateway host in the inventory:

```yaml
vpn_public_host: vpn.fleet.example.com   # devices get Endpoint = vpn.fleet.example.com:51820
```

Without it, devices dial the host's IP (`ansible_host`). The demo/E2E Terraform
creates `wg.<env>.<domain>` itself. `vpn` and `wg` are reserved, so no app can take them.

Reconnecting: devices send a keepalive every 25s, so after a reboot, a network
change on the device, or a short outage, WireGuard reconnects on its own.
WireGuard looks a name up only when the tunnel starts, so the device agent
checks on every heartbeat: when the last handshake is older than 3 minutes, it
looks `wg.<domain>` up again and points the tunnel at the new IP
(`wg set wg0 peer … endpoint …`). Moving the gateway to a new IP is then just a
DNS change; use a short TTL (60s).

The API and the agent share `CONNECTIVITY_INTERNAL_TOKEN`
(`EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN` on the controller). The agent
reaches `/internal/connectivity/*` privately; Traefik never serves `/internal`.

| Where the gateway runs | Inventory | Agent polls | API → devices (SSH, apps) |
| --- | --- | --- | --- |
| API host (default, `make up` / edge-installer) | `platform` | `127.0.0.1:8000` (`docker-compose.vpn.yml`) | host `wg0` |
| Own server, API managed by Ansible | `vpn_gateway` + `platform` | API over an automatic site link | site link `wg-link` |
| Own server, control plane elsewhere | `vpn_gateway` only | `vpn_control_plane_url` | `vpn_site_links` or `vpn_routed_cidrs` |

**On the API host.** Add `vpn` to `enabled_services`; `site.yml` sets up the
gateway first, then writes `CONNECTIVITY_PROVIDER=wireguard`, the server public
key, and the endpoint into the API's `.env`. Docker/Traefik on the host route
**to** `10.8.0.0/16` via `wg0`. New connections **from** VPN devices are dropped
(established replies are allowed), including Docker networks. There is no
MASQUERADE: devices cannot use the host as an internet gateway.

**On a dedicated server.** A dedicated gateway lets only the control plane open
connections to devices, and NATs that traffic to `10.8.0.1` (the only address
devices accept). Devices cannot open connections anywhere: not to the gateway,
not to other devices, not to the internet.

- *API managed by the same inventory:* each API host gets a WireGuard site link
  (`vpn_link_subnet`, default `10.9.0.0/24`; API host N is `.N+2`) and publishes
  the API on that address only. No VPC peering, routes, or EC2 source/dest-check
  changes; the gateway can be in a different network from the API.
- *Control plane elsewhere (other cloud, Kubernetes):* run
  `playbooks/wireguard.yml` with an inventory like
  `inventory/example-wireguard-aws/` or `inventory/example-wireguard-onprem/`.
  Set `vpn_control_plane_url` to a private address of the API. Then give the
  control plane a path to the devices, either:
  - `vpn_site_links`: generate a key on the control-plane host
    (`wg genkey | tee /etc/wireguard/link.key | wg pubkey`), list it with an
    address in `vpn_link_subnet`, and install the generated
    `wg-link-<name>.conf` there; or
  - `vpn_routed_cidrs`: the VPC or pod CIDR when the control plane routes to
    the gateway's private IP. On AWS, disable the instance's source/dest check
    and route `10.8.0.0/16` to its ENI. EKS pods have VPC IPs, so this covers
    Helm installs.

  The playbook writes `deploy/ansible/artifacts/wireguard-<gateway>/`:
  `control-plane.env` (Compose) and `helm-values.yaml` (`connectivity.*` for the
  Helm chart; `connectivity.gatewayService` adds an internal load balancer for
  the agent).

Manual agent run (static site-link peers are never removed):

```bash
CONNECTIVITY_INTERNAL_TOKEN=… \
  python3 src/device-plane/gateway/connectivity_agent.py \
    --api-url http://127.0.0.1:8000 --interface wg0 [--keep-peer <site-link-pubkey>]
```

See [`deploy/ansible/README.md`](../../deploy/ansible/README.md#wireguard-gateway).

## Device side

Install `wireguard` / `wireguard-tools` on the Pi. `meteorcli run` (as the
device user; `installcli.sh` allows passwordless `sudo` for `wg` / `wg-quick`):

1. Generates a keypair under `~/.config/meteorcli/wireguard/` (or `/etc/meteorcli` for the service)
2. POSTs the **public** key, this login (`pi`, `ubuntu`, …) and the sshd **public** host key
   (`/etc/ssh/ssh_host_ed25519_key.pub`) to `/api/v1/agent/connectivity/wireguard`
3. Writes `wg0.conf` and runs `wg-quick up`
4. Installs `SHELL_GATEWAY_PUBLIC_KEY` into that user's `authorized_keys` when configured

The key and the address never change on reconnects or restarts. The agent
registers at start and then every ~15 minutes, not on every heartbeat. If that
fails, it retries with exponential backoff and jitter (30 s doubling up to 30 min).
On every heartbeat, without calling the API, it brings `wg0` back up from the
local `wg0.conf` and looks up the gateway's DNS name again when handshakes stop.
So a device recovers on its own after a reboot, a lost uplink, a gateway IP
change, or a control-plane outage.

`sshd` must be running for browser SSH. Do not forward port 22 on the home
router.

## WireGuard network on the device page

The device page's **WireGuard network** card shows the device's internal IP
(e.g. `10.8.0.2`), whether its tunnel is connected (a recent handshake), and the
SSH command to reach it (`meteorcloud-ssh pi@10.8.0.2` when `meteorcli` runs as `pi`).
The agent reports that user; `SHELL_SSH_USER` (default `root`) is only the
fallback. On the MeteorCloud server (`make up` installs the helper):

```bash
meteorcloud-ssh pi@10.8.0.2
```

It also takes a device ID (`meteorcloud-ssh <device-id>`). With Docker access it
asks the API container about the target first, so revoked, disabled and offline
devices are refused with a clear message and the pinned host key is enforced.
Without Docker access it warns and connects to the given address, trusting its
host key on first use (`~/.config/meteorcloud/shell/known_hosts`).

That uses your copy of the gateway key (`~/.config/meteorcloud/shell/id_ed25519`,
mode `0600`). `make up` installs it. OpenSSH rejects a group-readable platform
key, so `/opt/edge-platform/shell/id_ed25519` stays `0600` for the browser
container only. ed25519 is the key algorithm, not a password.

Devices cannot reach each other; only the server (`10.8.0.1`) can reach them.

### States

| State | Meaning |
| --- | --- |
| `connected` | The gateway saw a handshake in the last 3 minutes |
| `reconnecting` | No recent handshake, but the agent still checks in: the tunnel is down and the agent is bringing it back |
| `offline` | No recent handshake and no recent check-in |
| `pending` | Waiting for the agent to register a key (new device or after re-enroll) |
| `revoked` | Access removed; the device cannot reconnect until re-enrolled |

A handshake shows that the tunnel works, not that SSH or an app does. The card
also shows the agent's last check-in, the pinned SSH host-key fingerprint, and
the last browser SSH session or the error ssh printed.

### Revoke, disable, re-enroll

- **Revoke access** (`POST …/connectivity/revoke`) drops the peer at the next
  gateway sync (~15 s), disables the device's app endpoints and refuses new
  registrations. Disabling the device or revoking its credential also removes the
  peer and its app routes, and re-enabling it restores them.
- **Re-enroll** (`POST …/connectivity/reenroll`, `device.update`) is the explicit
  recovery path for a replaced or reinstalled device, or one that was revoked by
  mistake. It forgets the WireGuard key, address and pinned host key. The agent's
  next scheduled check (≤ 30 min, or restart `meteorcli`) registers again with
  its own credential and gets a new address.
- A device that presents a **different WireGuard key** while active is refused
  (`wireguard_key_changed`, audited) until an operator re-enrolls it. Keys never
  rotate on their own.

### SSH host keys

The first host key a device reports over its authenticated registration is
pinned. Browser SSH and `meteorcloud-ssh` then use `StrictHostKeyChecking=yes`
against that key only, so a changed key fails instead of being trusted. If the
device reports a different key later, for example after an OS reinstall, the pin
is kept and the card shows the mismatch. Check the device, then **Re-enroll** it
to trust the new key.

Migration: devices running an older agent never report a host key and keep
trust-on-first-use (`accept-new`). Update `meteorcli` on them. Once every device
reports a key, set `SHELL_REQUIRE_HOST_KEY=true` to refuse unpinned devices.

### Network isolation and ports

Inbound ports:

| Host | Port | From |
| --- | --- | --- |
| WireGuard gateway | UDP `51820` (`vpn_listen_port`) | devices (internet) |
| Platform | TCP `80`, `443`; TCP `8883` with bundled MQTT | internet |
| Any host | TCP `22` | `firewall_allowed_ssh_cidrs` only |

Device SSH (`22`) is never published. It is reachable only through the tunnel,
and only from the gateway side. Both gateway layouts (dedicated or co-located)
apply the same `wg0` rules: devices may only answer connections. They cannot
reach other devices, the gateway host, the internet, or containers (`DOCKER-USER`).
App endpoints cannot use the device SSH port. AllowedIPs are IPv4 `/32`s and
IPv6 forwarding stays off, so IPv6 packets from devices are dropped. On a
co-located host, `wg-quick@wg0` starts after Docker so the `DOCKER-USER` rules
always apply. The rules live in `wg0.conf` (`PostUp`), so they come back on every
boot and on every `wg-quick` restart.

## Browser terminal

Members with `device.shell` (owner and admin only) open **Terminal** on the
device page. Flow: JWT → short-lived ticket → WebSocket → SSH gateway →
`ssh user@{wg_address}` over WireGuard.

Tickets live 60 s and work once. The console sends the ticket in the first
WebSocket frame, never in the URL, so it stays out of access logs (`?ticket=` still
works for older clients, but it is redacted from the API's logs). A wrong, expired or used ticket is refused.
`device.shell` is checked again when the ticket is used, and the device must be
enabled and have a recent handshake (`device_offline` otherwise).

`make up` with WireGuard turns this on (`SHELL_PROVIDER=ssh_gateway` and a
`0600` gateway key and SSH CA under `/opt/edge-platform/shell`). The API host
must be able to reach `10.8.0.0/16`. Re-run `meteorcli run` on each device so it
installs the CA (and, until it is retired, the gateway key); see
[SSH certificates](#ssh-certificates-instead-of-the-shared-key). Tests use `SHELL_PROVIDER=echo`. Set
`shell_provider: none` in inventory if you want the tunnel without the terminal.

## Test locally (no real tunnel)

Control-plane tests enable WireGuard IPAM with `CONNECTIVITY_PROVIDER=wireguard`
and an echo shell. They do **not** start `wg0`.

Manual gateway check:

1. `wg show` on the server after a device registers — a peer should appear
   within ~15s of the gateway agent poll.
2. From the server: `ping 10.8.0.x` then the SSH command from the device page.
3. Console → device → Connect in Terminal.
4. Revoke connectivity in the API; `wg show` should drop the peer and new
   shells should fail.

## DNS for apps (not SSH)

All DNS records are listed in [DNS records](../dns.md). SSH needs none of its
own. App URLs need one **wildcard**
`*.<APPS_BASE_DOMAIN>` A/AAAA (or CNAME) to the gateway. It covers custom names
(`shop.meteor-edge.com`) and suggested ones (`shop.kiosk-12.acme.meteor-edge.com`).
Records that already exist under the domain (for example `www`) take precedence;
list them in `APPS_RESERVED_NAMES`.

Let’s Encrypt **HTTP-01** stays on `PLATFORM_DOMAIN`. App hostnames use Traefik
**DNS-01** (`letsencrypt-dns`, default Route53). Put AWS keys in host env, never
in git. See [Endpoints](endpoints.md).

## SSH certificates instead of the shared key

Every browser terminal and `meteorcloud-ssh` session logs in with a key that
exists for that session only, plus a certificate signed by the server's SSH user
CA. A certificate:

- is valid for about 2 minutes (`SHELL_SSH_CERT_TTL_SECONDS`), which only
  matters at login;
- names one device (principal `meteorcloud-device-<id>`). Devices trust the CA
  only for their own principal, so it can't open any other device;
- allows a PTY only, with no agent, port or X11 forwarding;
- carries a key ID (`shell:<session>:<user email>` or `cli:<user>@<host>`), which
  shows up in the device's `auth.log`. `meteorcloud-ssh` certificates are also
  in the audit log (`device.shell.certificate`).

The device side needs no sshd change and no root. The agent adds one line to
`authorized_keys`:

```text
cert-authority,principals="meteorcloud-device-<id>" ssh-ed25519 AAAA… meteorcloud-ca
```

The agent manages only lines ending in `meteorcloud-ca` or
`meteorcloud-gateway` (or the older `meteorcloud-shell-gateway`). It never
touches your own keys.

**Rollout**

1. **Both mechanisms (default).** `make up` creates `/opt/edge-platform/shell/ca`
   (0600) next to the gateway key and sets `SHELL_SSH_CA_FILE` and
   `SHELL_SSH_CA_PUBLIC_KEY`. Updated agents install the CA line and report it.
   Their device page then shows **SSH auth: certificate**. Devices with older
   agents keep using the shared key. On Kubernetes, add `ca` and `ca.pub` to the
   `connectivity.shell.existingSecret` Secret and set
   `connectivity.shell.certificates: true`.
2. **Retire the shared key.** Do this once every device shows certificate: set
   `shell_gateway_key_retired: true` (Helm:
   `connectivity.shell.gatewayKeyRetired: true`) and re-run `make up`.
   - The server stops sending the gateway key, and agents remove its line.
   - Sessions to devices that are still on the shared key are refused with
     `shell_certificate_required`.
   - Ansible deletes the operators' copies in `~/.config/meteorcloud/shell/`.

Keep the CA and gateway private keys `0600`. They are never sent to devices or
the browser. To rotate the CA, replace `ca` and `ca.pub` and re-run `make up`.
Agents replace their CA line on the next registration (within about 15 minutes).
Until they do, those devices use the shared key, or are refused after phase 2.

## Related

- [Endpoints](endpoints.md)
- [Deployments](deployments.md)
- [Heartbeat](heartbeat.md)
- [Services](../services.md) (`vpn`)
