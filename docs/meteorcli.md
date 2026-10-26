# meteorcli

Device-plane CLI. Runs on managed Linux devices: configure the control plane, enroll, heartbeat, and keep an MQTT session.

Package details and install script: [`src/device-plane/agent/README.md`](../src/device-plane/agent/README.md).

## Install

On the device (does **not** clone this repository):

```bash
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | bash
# system-wide + systemd:
# curl -fsSL .../install-meteorcli.sh | sudo bash -s -- --with-service
meteorcli --help
```

Pin a version: `METEORCLI_VERSION=0.1.0` (must match a published `agent-v*` tag).

**Publish a release** (maintainers): set the version in
`src/device-plane/agent/pyproject.toml`, then:

```bash
make agent-dist          # optional local check; writes dist/
git tag agent-v0.1.0     # must match the pyproject version
git push origin agent-v0.1.0
```

CI packs agent + `src/device-plane/runtime` (no tests, no secrets) and creates
the GitHub Release. Laptop / from a checkout: `cd src/device-plane/agent && ./installcli.sh`.

Config directory: `~/.config/meteorcli`, or `/etc/meteorcli` as root. With
`meteorcli.service` installed, `sudo meteorcli …` also uses `/etc/meteorcli`, the
directory the service reads.

## Commands

| Command | Purpose |
| --- | --- |
| `config` | Store control-plane domain / API base and enrollment API key |
| `test` | Reachability + API key check |
| `register` | Enroll with a registration token |
| `request-token` | Device-initiated enroll; poll for approval |
| `claim` | Fetch device token after a later approval |
| `run` | Foreground loop: heartbeats, MQTT, WireGuard (`--once` for a single pass). Stays in the terminal (Ctrl+C to stop). Refuses if `meteorcli.service` is already active so you do not run two agents |
| `status` | Show non-secret local config, including systemd and WireGuard |
| `mqtt-test` / `mqtt-listen` | MQTT diagnostics |

Env overrides: `METEORCLI_DOMAIN`, `METEORCLI_SERVER`, `METEORCLI_API_KEY`, `METEORCLI_TOKEN`, `METEORCLI_CONFIG_DIR`.

`--domain` is the **API host** (no `api.` subdomain). IP/localhost uses HTTP; names default to HTTPS unless `--http` or `--api-base` / `METEORCLI_SERVER` is set.

## Configure

```bash
meteorcli config --domain localhost:8000 --api-key key_...
meteorcli test
meteorcli config --show
```

## Enroll

**Registration token:**

```bash
meteorcli register --server http://localhost:8000 --token-file /tmp/meteorcli.token --name edge-01
```

**Device-initiated:**

```bash
meteorcli request-token --name edge-01   # waits up to ~5 minutes (--wait)
# if still pending after approval later:
meteorcli claim
```

Then start the agent. Prefer the systemd unit on a fleet device; use `meteorcli run` only in the foreground:

```bash
sudo systemctl enable --now meteorcli   # background; logs: journalctl -u meteorcli -f
meteorcli status
# or, if the service is not installed:
meteorcli run                           # foreground; Ctrl+C to stop
meteorcli run --once                    # one heartbeat (allowed even if the service is running)
```

`meteorcli run` is not a daemon. If `meteorcli.service` is already active, it exits instead of starting a second copy. Stop the unit first (`sudo systemctl stop meteorcli`) when you need a foreground session.

## Related

- [Getting started](getting-started.md)
- [Registration tokens](fleet/registration-tokens.md)
- [Device-initiated enrollment](fleet/device-request-enrollment.md)
- [Heartbeat](fleet/heartbeat.md)
- [MQTT](fleet/mqtt.md)
