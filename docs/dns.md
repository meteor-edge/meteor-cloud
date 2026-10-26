# DNS records

MeteorCloud is reached by **your domain**, not a raw IP. `make up` asks which
optional services you need, then prints **only the DNS records for that setup**.
Create them as plain A records (**no proxy**; in Cloudflare: grey cloud). Let's
Encrypt and WireGuard need to hit the server directly. A short TTL (60–300s)
makes moving to a new server quick.

```bash
make up PLATFORM_DOMAIN=fleet.example.com
```

Replace `fleet.example.com` with your domain.

## Records per service

Only create a row when that service is enabled.

| Service | When | DNS name (A) | Points to | Ports |
| --- | --- | --- | --- | --- |
| `cloud_app` | always | `fleet.example.com` | this server | TCP 80, 443 |
| `cloud_app` (apps) | always | `*.fleet.example.com` | this server | TCP 443 |
| `vpn` | WireGuard enabled | `vpn.fleet.example.com` | WireGuard server | UDP 51820 |
| `mqtt` | bundled EMQX | `mqtt.fleet.example.com` | this server | TCP 8883 |
| `mqtt` | external broker | none on this server | your broker | — |
| storage | filesystem or MinIO | none (not public) | downloads via `https://fleet.example.com/api` | — |
| storage | S3 / GCS | none on this server | your bucket; API still proxies downloads | — |

`vpn` and `wg` are reserved so an app cannot take them. `wg.fleet.example.com`
still works if you set `vpn_public_host` to it.

You can put the platform on a subdomain such as `cloud.example.com`; the records
are then `cloud.example.com`, `*.cloud.example.com` and `vpn.cloud.example.com`.

## What each record does

**`<domain>`: console and API (`cloud_app`).** Traefik gets a Let's Encrypt
certificate for it over HTTP (port 80 must be open). Operators open
`https://<domain>`. Devices register at `https://<domain>/api`.

**`*.<domain>`: apps (`cloud_app`).** One wildcard covers every app address, at
any depth: custom names (`shop.fleet.example.com`) and suggested ones
(`shop.kiosk-12.acme.fleet.example.com`). Records that already exist, such as
`www`, win over the wildcard; list them in `APPS_RESERVED_NAMES` so no app can
claim them. Certificates for apps are issued over DNS (`_acme-challenge` TXT
records), which Traefik writes itself with the Route53 credentials it is given.
Apps can live on another domain with `APPS_BASE_DOMAIN`; the wildcard then goes
there. See [Endpoints](fleet/endpoints.md).

**`vpn.<domain>`: WireGuard (`vpn`).** Devices dial `vpn.<domain>:51820` to join
the private network the browser terminal and app traffic run over. Point it at
the same server, or at the dedicated gateway if WireGuard runs on its own
machine (`placement: dedicated`). Tell the installer the name rather than the
IP, on the WireGuard host in the Ansible inventory:

```yaml
vpn_public_host: vpn.fleet.example.com
```

When the server's IP changes, update the record; devices look the name up again
once their tunnel goes quiet (about 3 minutes) and reconnect. See
[Connectivity](fleet/connectivity.md).

## Where you see this list

- **`make aws-up ARGS='--route53'`** asks for the domain, finds the public Route53 zone
  that contains it, and Terraform creates `<domain>`, `*.<domain>`, and
  `wg.<domain>`. Or set `platform.domain` and `aws.dns_zone_id` in
  `installation.yaml`.
- **`make up`** asks for the domain. With `--route53` and the AWS CLI it creates
  the missing records in the matching public hosted zone, and stops if a name
  already points elsewhere; otherwise it prints them.
- **`make up`** prints the records (with the public IP it detected) before and
  after the install.
- **Ansible** prints them at the end of every install
  (`Show the DNS records this installation needs`).
- **Full-stack E2E** (`tests/e2e/meteorcloud-testenv status`) prints them too. With
  `E2E_DNS_PROVIDER=route53` Terraform creates the zone names; with `manual` it
  prints them right after the servers exist so you can add them.

## Check

```bash
dig +short fleet.example.com
dig +short anything.fleet.example.com     # any name: tests the wildcard
dig +short vpn.fleet.example.com
curl -sS https://fleet.example.com/health
```

All `dig` answers should be the expected public IPs. If the console loads but
apps get certificate errors, the wildcard or the Route53 credentials are
missing. If devices are online but show "Waiting for the device" under
**WireGuard network**, check `vpn.<domain>` and that UDP 51820 is open.
