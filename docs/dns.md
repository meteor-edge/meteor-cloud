# DNS records

MeteorCloud needs **three DNS names** under your domain. Create them before you
install, so the HTTPS certificates can be issued on the first try.

| Record (A) | Points to | Used for | Ports |
| --- | --- | --- | --- |
| `meteor-edge.com` | the server's public IP | console, API (`/api`), MQTT for devices | TCP 80, 443, 8883 |
| `*.meteor-edge.com` | the server's public IP | public app URLs | TCP 443 (80 for redirects) |
| `wg.meteor-edge.com` | the WireGuard server's public IP | devices connect their private tunnel here | UDP 51820 |

Replace `meteor-edge.com` with your domain (`PLATFORM_DOMAIN`). You can also use
a subdomain such as `cloud.example.com`; the records are then
`cloud.example.com`, `*.cloud.example.com` and `wg.cloud.example.com`.

There are no separate names for the backend or the data plane: the API is
served at `https://<domain>/api` next to the console, and devices reach MQTT at
`<domain>:8883`.

Make them plain records: **DNS only, no proxy** (in Cloudflare: grey cloud).
WireGuard and MQTT are not HTTP, and Let's Encrypt must reach the server
directly. A short TTL (60–300s) makes moving to a new server quick.

## What each record does

**`<domain>`: console, API and MQTT.** Traefik gets a Let's Encrypt certificate
for it over HTTP (port 80 must be open). Devices register at
`https://<domain>/api` and connect to MQTT at `<domain>:8883`.

**`*.<domain>`: apps.** One wildcard covers every app address, at any depth:
custom names (`shop.meteor-edge.com`) and suggested ones
(`shop.kiosk-12.acme.meteor-edge.com`). Records that already exist, such as
`www`, win over the wildcard; list them in `APPS_RESERVED_NAMES` so no app can
claim them. Certificates for apps are issued over DNS (`_acme-challenge` TXT
records), which Traefik writes itself with the Route53 credentials it is given.
Apps can live on another domain with `APPS_BASE_DOMAIN`; the wildcard then goes
there. See [Endpoints](fleet/endpoints.md).

**`wg.<domain>`: WireGuard.** Devices dial `wg.<domain>:51820` to join the
private network the browser terminal and app traffic run over. Point it at the
same server, or at the dedicated gateway if WireGuard runs on its own machine
(`placement: dedicated`). Tell the installer to hand out the name rather than
the IP, on the WireGuard host in the Ansible inventory:

```yaml
vpn_public_host: wg.meteor-edge.com
```

The name `wg` is reserved, so no app can take it. When the server's IP changes,
update the record; devices look the name up again once their tunnel goes quiet
(about 3 minutes) and reconnect. See [Connectivity](fleet/connectivity.md).

## Where you see this list

- **Ansible** prints the records, with the IPs it knows, at the end of every
  install (`Show the DNS records this installation needs`).
- **Demo / E2E** (`tests/e2e/demo.sh status`) prints them too. With
  `E2E_DNS_PROVIDER=route53` Terraform creates all three; with `manual` it prints
  them right after the servers exist so you can add them.

## Check

```bash
dig +short meteor-edge.com
dig +short anything.meteor-edge.com     # any name: tests the wildcard
dig +short wg.meteor-edge.com
curl -sS https://meteor-edge.com/health
```

All three `dig` answers should be the expected public IPs. If the console loads
but apps get certificate errors, the wildcard or the Route53 credentials are
missing. If devices are online but show "Waiting for the device" under
**WireGuard network**, check `wg.<domain>` and that UDP 51820 is open.
