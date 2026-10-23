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

Ansible installs everything on an Ubuntu 22.04/24.04 or Debian 12 host:
`wireguard`, `wireguard-tools`, `iptables`, the kernel module, and persistent
IPv4 forwarding. It generates the server key **on the gateway** once
(`/etc/wireguard/server.key`; `wg0.conf` loads it in `PostUp` and never contains
it) and starts `wg-quick@wg0` plus the **peer gateway agent**
(`meteorcloud-gateway.service`). The agent applies `wg set` from the control
plane every 15s and never rewrites `wg0.conf`; after a `wg-quick` restart it
re-adds peers on the next poll. Open **UDP 51820** to the gateway (security
group, or a router port forward on-prem).

### The address devices dial: `wg.<domain>`

One of the three [DNS records](../dns.md) MeteorCloud needs.

Give the gateway a DNS name, e.g. `wg.meteor-edge.com`, as an **A record to its
public IP** (DNS only: no HTTP proxy such as Cloudflare's orange cloud, since
WireGuard is UDP). Set it on the gateway host in the inventory:

```yaml
vpn_public_host: wg.meteor-edge.com   # devices get Endpoint = wg.meteor-edge.com:51820
```

Without it, devices dial the host's IP (`ansible_host`). The demo/E2E Terraform
creates `wg.<env>.<domain>` itself. `wg` is reserved, so no app can take it.

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
| API host (default, edge-installer) | `platform` | `127.0.0.1:8000` (`docker-compose.vpn.yml`) | host `wg0` |
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

Install `wireguard` / `wireguard-tools` on the Pi. `meteorcli run` (as root):

1. Generates a keypair under `/etc/meteorcli/wireguard/`
2. POSTs the **public** key to `/api/v1/agent/connectivity/wireguard`
3. Writes `wg0.conf` and runs `wg-quick up`
4. Installs `SHELL_GATEWAY_PUBLIC_KEY` into `authorized_keys` when configured

`sshd` must be running for browser SSH. Do not forward port 22 on the home
router.

## WireGuard network on the device page

The device page's **WireGuard network** card shows the device's internal IP
(e.g. `10.8.0.2`), whether its tunnel is connected (a recent handshake), and the
SSH command to reach it, e.g. `ssh root@10.8.0.2` (`SHELL_SSH_USER`, plus `-p`
when `SHELL_SSH_PORT` is not 22). Run it on the MeteorCloud server with the
browser-terminal key, which every device trusts once it joins:

```bash
sudo ssh -i /opt/edge-platform/shell/id_ed25519 root@10.8.0.2   # Ansible installs
```

Devices cannot reach each other; only the server (`10.8.0.1`) can reach them.

## Browser terminal

Operators with `device.shell` (owner/admin/operator) open **Terminal** on the
device page. Flow: JWT → short-lived ticket → WebSocket → SSH gateway →
`ssh user@{wg_address}` over WireGuard.

Set `SHELL_PROVIDER=ssh_gateway` and `SHELL_SSH_IDENTITY_FILE` on the API host
(the process must be able to reach `10.8.0.0/16`). Tests use `SHELL_PROVIDER=echo`.

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

## Related

- [Endpoints](endpoints.md)
- [Deployments](deployments.md)
- [Heartbeat](heartbeat.md)
- [Services](../services.md) (`vpn`)
