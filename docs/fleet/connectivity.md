# Device connectivity (WireGuard) and browser SSH

MeteorCloud reaches devices on a **private WireGuard network**. Devices dial out
to the gateway (NAT-friendly). SSH is never published on the public internet.
Public app URLs (`*.appx…`) are separate; see [Endpoints](endpoints.md).

This is implemented on the **Compose + Ansible (EC2) path**. Kubernetes stays
`CONNECTIVITY_PROVIDER=none` until a dedicated gateway exists.

## What is stored where

| Secret | Where |
| --- | --- |
| Device WireGuard **private** key | Device only (`/etc/meteorcli/wireguard/private.key`) |
| Device WireGuard **public** key | Device + control plane |
| Server WireGuard **private** key | Cloud host env `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY` |
| SSH gateway private key | Cloud host (`SHELL_SSH_IDENTITY_FILE`) |
| Browser | Short-lived `sh_…` ticket only |

If an SD card dies, re-enroll the device, revoke the old peer, and register a
new WireGuard public key. The control plane cannot restore the lost private key.

## Set up the WireGuard server (EC2 / Compose host)

1. Enable the installer `vpn` service (default on AWS). Open **UDP 51820** on
   the security group.
2. Generate a server key **on the host** (never commit it):

   ```bash
   wg genkey | tee /etc/wireguard/server.key | wg pubkey > /etc/wireguard/server.pub
   chmod 600 /etc/wireguard/server.key
   ```

3. Set on the Ansible controller / host env:

   ```bash
   export EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY="$(cat /etc/wireguard/server.key)"
   ```

   Re-deploy so Ansible renders `/etc/wireguard/wg0.conf` (Interface only) and
   starts `wg-quick@wg0`.

4. Point the API at that gateway (Ansible does this when the VPN key is set):

   ```bash
   export EDGE_PLATFORM_VPN_SERVER_PUBLIC_KEY="$(cat /etc/wireguard/server.pub)"
   export EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN="<random>"

   CONNECTIVITY_PROVIDER=wireguard
   WIREGUARD_SERVER_PUBLIC_KEY="$(cat /etc/wireguard/server.pub)"
   WIREGUARD_SERVER_ENDPOINT="<public-ip-or-dns>:51820"
   WIREGUARD_CLIENT_CIDR=10.8.0.0/16
   WIREGUARD_SERVER_ADDRESS=10.8.0.1/16
   CONNECTIVITY_INTERNAL_TOKEN="<random>"
   ROUTING_INTERNAL_TOKEN="$CONNECTIVITY_INTERNAL_TOKEN"
   ```

5. Run the **peer gateway agent** on the same host (applies `wg set` from the
   control plane; does not rewrite `wg0.conf` peer lists). Ansible installs
   `meteorcloud-gateway.service`. Manual run:

   ```bash
   CONNECTIVITY_INTERNAL_TOKEN=… \
     python3 src/device-plane/gateway/connectivity_agent.py \
       --api-url http://127.0.0.1:8000 --interface wg0
   ```

   After a `wg-quick` restart the agent re-adds peers on the next poll. Production
   Compose binds the API on `127.0.0.1:8000` for this agent (Traefik still owns 80/443).

6. Enable IPv4 forwarding (the `wg0` PostUp does this). Docker/Traefik on the
   host can then route to `10.8.0.0/16` via the host’s `wg0`.

## Device side

Install `wireguard` / `wireguard-tools` on the Pi. `meteorcli run` (as root):

1. Generates a keypair under `/etc/meteorcli/wireguard/`
2. POSTs the **public** key to `/api/v1/agent/connectivity/wireguard`
3. Writes `wg0.conf` and runs `wg-quick up`
4. Installs `SHELL_GATEWAY_PUBLIC_KEY` into `authorized_keys` when configured

`sshd` must be running for browser SSH. Do not forward port 22 on the home
router.

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
2. From the server: `ping 10.8.0.x` then `ssh root@10.8.0.x`.
3. Console → device → Connect in Terminal.
4. Revoke connectivity in the API; `wg show` should drop the peer, open
   shells should end within `SHELL_RECHECK_SECONDS`, and new shells should fail.

## Revoke and restore

`POST …/devices/{id}/connectivity/revoke` removes the peer, disables the
device's endpoints, and closes its shells. The device keeps its address
reserved, and its agent gets `403 connectivity_revoked` and takes its tunnel
down. It stays down until an operator calls
`POST …/devices/{id}/connectivity/restore`. After that, the next registration
(the agent retries about every 10 minutes, or right away on restart) brings it
back on the same address.

The agent registers once at start and then every 10 minutes. It runs
`wg-quick` only when the rendered `wg0.conf` changes or the interface is down,
so heartbeats never restart the tunnel.

## Container traffic and isolation

Traefik and the SSH gateway run in Docker, so their packets leave on `wg0` with
a Docker source address. Devices only accept tunnel traffic from the server
address, so `wg0.conf` masquerades non-tunnel sources to `10.8.0.1`. It also
drops `wg0 → wg0` forwarding, so devices cannot reach each other. The gateway
SSH key is installed with `from="10.8.0.1/32"`. Host keys are kept in one
`known_hosts` file per device under `SHELL_KNOWN_HOSTS_DIR`.

## DNS for apps (not SSH)

SSH does not need public DNS. App URLs need a **wildcard**
`*.appx.<APPS_BASE_DOMAIN>` A/AAAA (or CNAME) to the gateway. A wildcard on
`*.meteor-edge.com` does **not** cover `boiler.appx.meteor-edge.com`.

Let’s Encrypt **HTTP-01** stays on `PLATFORM_DOMAIN`. App hostnames use Traefik
**DNS-01** (`letsencrypt-dns`, default Route53). Put AWS keys in host env, never
in git. See [Endpoints](endpoints.md).

## Related

- [Endpoints](endpoints.md)
- [Deployments](deployments.md)
- [Heartbeat](heartbeat.md)
- [Services](../services.md) (`vpn`)
