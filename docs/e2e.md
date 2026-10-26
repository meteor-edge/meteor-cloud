# Full-stack E2E environment

`tests/e2e/meteorcloud-testenv` builds a real MeteorCloud environment from your
working tree and proves the product works end to end. It drives the real API,
the real `meteorcli` on separate device machines, and the product's own
Terraform and Ansible.

```text
meteorcloud-testenv up
  validate    settings, tools, AWS identity, Route53 zone ownership
  terraform   control plane EC2 (+ dedicated WireGuard gateway), N device EC2s,
              DNS <env>.<domain> and *.<env>.<domain>, Traefik DNS-01 role
  ansible     site.yml (cloud_app + vpn) and device.yml, run twice (2nd: changed=0)
  suite       61 checks in 11 stages -> report.json + summary.txt
```

| Command | What it does |
| --- | --- |
| `validate` | Static settings, required tools, AWS credentials, the Route53 zone matches `E2E_DOMAIN` |
| `up` | `validate` + provision + configure + `test`. Keeps the environment on failure. `--no-tests` stops after configure |
| `seed` | Demo data to explore by hand: *Demo Organization*, a demo owner account, every device registered and on WireGuard, `demo-app` 1.0.0 and 2.0.0 on device 1 at its suggested address `https://demo-app.demo-device-1.demo-organization.<env>.<domain>`, and `hello` on device 2 at the custom address `https://hello.<env>.<domain>` |
| `test` | Run the acceptance suite again against the existing environment (new run id) |
| `status` | Hosts, SSH commands, URL, `/health`, last result |
| `logs` | Redacted service logs, `wg show`, iptables, the routing table, into `.state/<env>/logs/` |
| `down` | `terraform destroy` of this environment's state only (asks for the name, or `--yes`) |

`up` and `test` take `--only "<stage or check id>"` (repeatable) and
`--destroy-on-failure`. Exit codes: `0` all required checks passed
(`not_supported` is allowed), `1` a check failed or was blocked, `2`
configuration or prerequisites, `3` infrastructure.

## Quick start (AWS)

```bash
cp tests/e2e/.env.example tests/e2e/.env   # set E2E_ENV_NAME, E2E_DNS_ZONE_ID, E2E_DOMAIN
tests/e2e/meteorcloud-testenv validate
tests/e2e/meteorcloud-testenv up               # ~30-45 min; images are built on the control plane
tests/e2e/meteorcloud-testenv status
tests/e2e/meteorcloud-testenv down
```

Needs Python 3.10+ (standard library only), Terraform 1.7+, Ansible core 2.15+
with `community.general`, OpenSSH and git. AWS credentials come from the usual
`AWS_*` variables or `AWS_PROFILE`; the IAM user needs EC2, Route53 (the test
zone) and IAM (role + instance profile for DNS-01).

Cost: one `t3.medium` control plane, `t3.micro` devices (and gateway), EIPs and
two Route53 records while the environment exists. Nothing runs after `down`.

## Product demo vs this lab

A single server does not need this lab. `make up` or `make aws-up` with
`--demo` (or the **Demo organization** checkbox) creates Demo Organization
for the admin login. Enroll devices yourself with `meteorcli`.

`seed` is only for this multi-VM lab (extra device VMs, DNS, and sample apps):

```bash
tests/e2e/meteorcloud-testenv up --no-tests   # machines only, ~25 min
tests/e2e/meteorcloud-testenv seed            # lab data, ~5-15 min
```

`seed` prints the console URL, the logins (`.state/<env>/demo-login.env`, the
admin in `secrets.env`), each device's public and WireGuard address and the
app URL. Running `test` afterwards re-registers the devices for its own run.

## What is tested

Every result is `pass`, `fail`, `blocked` (a required check failed or a
prerequisite was missing) or `not_supported` (the product has no such feature,
or this run did not configure it — for example no public domain). Only `fail`
and `blocked` fail the run. If every unmet dependency is `not_supported`, the
dependent check is `not_supported` too.

| Stage | Checks |
| --- | --- |
| 1 infrastructure | SSH with pinned host keys; second Ansible run changes nothing; no SG allows SSH from `0.0.0.0/0`; Postgres, Redis, API, MinIO, Traefik, Grafana ports closed from the internet; stack healthy |
| 2 dns and tls | `<env>.<domain>`, a custom and an `app.device.org` name resolve to the ingress; `/health` over a verified Let's Encrypt certificate; HTTP redirects to HTTPS; `/internal/*` not served publicly |
| 3 api and artifacts | Admin login + organization; MinIO healthy, bucket exists, no host ports; upload records size + SHA-256; the object in MinIO matches; ticket download matches; missing artifact is 404; MinIO rejects a wrong secret |
| 4 onboarding | `meteorcli register` with a one-time token; device listed; token reuse rejected; `meteorcli.service` heartbeats -> online; restart keeps the identity |
| 5 wireguard | Address + handshake; route via `wg0`; handshake reported by the gateway agent; counters grow; API container reaches the device; gateway restart recovers; wrong server key gets no tunnel; device cannot open connections to the gateway |
| 6 browser ssh | Shell ticket -> WebSocket -> command runs on the device; reused and wrong tickets rejected |
| 7 application delivery | Test app built and uploaded as a `docker` artifact; release v1 deployed with an endpoint; container runs; upstream over WireGuard; endpoint `ready`; public HTTPS with the wildcard certificate; app port closed on the device's public address |
| 7b device logs | The device lists its log sources on demand (the deployed container marked deployed); a journal search finds a line written on the device; a followed log streams a new line and stops when the session closes; the container's log opens; the log certificate forces the helper command; the helper refuses a source it does not list |
| 8 update | v2 replaces v1; the same public URL serves v2 |
| 9 fleet and tenancy | Device 2 onboards; second hostname routes to device 2 and the first still to device 1, with no proxy file changes; devices cannot reach each other; another organization cannot read, shell into, publish or revoke this organization's resources; revoking device 2 removes its peer and route |
| 10 cleanup | Deleting the endpoint removes the route; workload removal (no product API today: `not_supported`) |

The test app (`tests/e2e/testapp`) is a busybox HTTP server that returns
`{"run_id", "device_id", "version"}`, so every response proves which run,
device and release answered.

## DNS and TLS prerequisites

The suite never assumes you own a domain. With `E2E_DNS_PROVIDER=route53`,
`validate` checks that the zone `E2E_DNS_ZONE_ID` is accessible and is
`E2E_DOMAIN`. Terraform then creates only `<env>.<domain>`,
`*.<env>.<domain>` and `wg.<env>.<domain>` ([DNS records](dns.md); tracked in the environment's state; nothing else in
the zone is touched) and an instance role that may only write the two
`_acme-challenge` TXT records. Traefik reads it through IMDSv2 (hop limit 2), so
no static AWS keys reach the host.

`E2E_DNS_PROVIDER=manual`: create the three A records yourself; `up` and
`status` print them with the right IPs. `none` (or no
domain): everything else still runs; DNS, TLS and public-app checks are
`not_supported` and the run can pass. A running proxy is never taken as proof
that a public endpoint works. Set `E2E_DOMAIN` and `E2E_DNS_ZONE_ID` in GitHub
Actions when you want those checks required.

## On-prem

```bash
E2E_PROVIDER=onprem
E2E_ONPREM_CONTROL_PLANE=ubuntu@203.0.113.10
E2E_ONPREM_GATEWAY=ubuntu@203.0.113.11,10.0.0.11     # optional: dedicated gateway
E2E_ONPREM_DEVICES=pi@203.0.113.20;pi@203.0.113.21
E2E_SSH_KEY_FILE=~/.ssh/meteorcloud-lab
E2E_ONPREM_INGRESS_IP=203.0.113.10                   # public address for DNS (port-forward 80/443)
```

Hosts must be Ubuntu 22.04+/Debian 12+ or RHEL/Rocky/Alma 9+ with sudo; devices need Python 3.11+.
Terraform manages only the DNS records. Ansible and the suite are the same as on
AWS. For the wildcard certificate off AWS, export a scoped IAM key as
`EDGE_PLATFORM_TRAEFIK_AWS_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY`. `down` removes
the stack (data directories are kept) and stops `meteorcli` on the devices.

## Security model

- Settings live in `tests/e2e/.env` and `.state/<env>/` (both gitignored,
  `0700`/`0600`). Platform secrets are generated per environment into
  `secrets.env` and passed to Ansible as environment variables, never written to
  the inventory.
- SSH: the allow-list defaults to your current IP/32; `/0` is rejected by the
  config and by Terraform. Devices accept SSH only from it; WireGuard UDP only
  from the device addresses.
- Host keys: trust on first use into `.state/<env>/known_hosts`
  (`StrictHostKeyChecking=accept-new`); a changed key fails. Your
  `~/.ssh/known_hosts` and global settings are untouched. (The repository's
  `deploy/ansible/ansible.cfg` sets `host_key_checking = False`; the suite
  overrides it with `ANSIBLE_HOST_KEY_CHECKING=True`.)
- TLS is always verified (system CAs, plus `E2E_CA_FILE` if set).
- Logs, reports and terminal output pass through a redactor: known secret
  values, tokens, tickets, JWTs, PEM and WireGuard private keys are masked.
- Routing is never configured by the suite: app routes come only from endpoints
  the API validated (hostname under `<domain>`, upstream = the device's
  WireGuard address).
- `down` destroys only resources in this environment's Terraform state (tagged
  `Project=meteorcloud-e2e`, `E2EEnvironment=<env>`). Failures keep the
  environment unless `--destroy-on-failure`.

## CI

- Pull requests (`ci.yml`, job *Full-stack E2E tooling*): unit tests of the
  orchestrator, `terraform validate` of `tests/e2e/terraform/*`, shellcheck, and
  `device.yml` in the Ansible syntax check. No credentials.
- **Full-stack E2E** (`.github/workflows/e2e-full.yml`, manual): runs tests and
  infra checks, then `validate`, `up`, `logs`, uploads `report.json`,
  `summary.txt` and the logs as an artifact, and always runs `down --yes`
  unless *keep_environment* is ticked. Inputs: gateway placement, device count.
  Secrets `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`; variables
  `E2E_DNS_ZONE_ID`, `E2E_DOMAIN` (optional `AWS_REGION`, `E2E_ACME_EMAIL`).

The older [EC2 smoke test](aws-ci.md) (`aws-ci.yml`) stays as a quick
installer check.

## Troubleshooting

- `status` prints ready-to-paste SSH commands for every host.
- `logs` gathers `docker compose logs`, `journalctl` for `meteorcli`,
  `wg-quick@wg0`, `meteorcloud-gateway`, `wg show`, iptables and the Traefik
  routing table.
- Ansible and Terraform output is in `.state/<env>/logs/{ansible-*,terraform}.log`.
- Let's Encrypt limits duplicate certificates to 5 per week per name set; reuse
  an environment (`test`) instead of recreating it many times a day.
