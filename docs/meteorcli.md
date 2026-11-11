# meteorcli

Device-plane CLI. Runs on managed Linux devices: configure the control plane, enroll, heartbeat, and keep an MQTT session.

Package details and install script: [`src/device-plane/agent/README.md`](../src/device-plane/agent/README.md).

## Install

On the device (does **not** clone this repository):

```bash
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | sudo bash
meteorcli --help
```

A root install also installs, enables and **starts** `meteorcli.service`. Until the
device is configured and registered the service only waits (it logs what to run
next). As soon as a token is stored or an enrollment request is approved, it
connects: heartbeats, MQTT and WireGuard, with no `meteorcli run`. Re-running the
installer upgrades and restarts the service.

- `--no-service` skips the service (CLI only).
- Without root, or where systemd is not running (most containers), there is no
  service; use `meteorcli run` in the foreground. `--with-service` makes that an
  error instead of a note.

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

Config directory: with `meteorcli.service` installed, always `/etc/meteorcli`,
the directory the service reads, so run enrollment commands with `sudo`
(without it, meteorcli says so). Without the service: `~/.config/meteorcli`, or
`/etc/meteorcli` as root.

## Commands

| Command | Purpose |
| --- | --- |
| `config` | Store control-plane domain / API base and enrollment API key |
| `test` | Reachability + API key check |
| `register` | Enroll with a registration token |
| `request-token` | Device-initiated enroll; poll for approval |
| `claim` | Fetch device token after a later approval |
| `run` | The agent loop: heartbeats, MQTT, WireGuard (`--once` for a single pass). In a terminal it stays in the foreground (Ctrl+C to stop) and refuses if `meteorcli.service` is already active. As the service it first waits for setup, registration and approval |
| `status` | Show non-secret local config, including systemd and WireGuard |
| `mqtt-test` / `mqtt-listen` | MQTT diagnostics |
| `logs` | List log sources (`--json`), or print one: `meteorcli logs systemd:nginx.service -f --grep error`. See [Device logs](fleet/logs.md) |

Env overrides: `METEORCLI_DOMAIN`, `METEORCLI_SERVER`, `METEORCLI_API_KEY`, `METEORCLI_TOKEN`, `METEORCLI_CONFIG_DIR`.

`--domain` is the **API host** (no `api.` subdomain). IP/localhost uses HTTP; names default to HTTPS unless `--http` or `--api-base` / `METEORCLI_SERVER` is set. For laptop `make dev`, include the control-plane port: `--domain 192.168.0.118:8000`. A bare IP is port 80 and will 404.

Changing `--domain` to a different server clears the previous device token and `mqtt.json` (those pointed at the old cloud). Re-register on the new API.

Uninstall (`./installcli.sh --uninstall`) removes the install **and** stored credentials (`~/.config/meteorcli` or `/etc/meteorcli`), so a later install does not keep talking to a previous MQTT broker.

## Configure

With `meteorcli.service` installed (root install), the config lives in
`/etc/meteorcli`, so use `sudo`:

```bash
sudo meteorcli config --domain localhost:8000 --api-key key_...
sudo meteorcli test
sudo meteorcli config --show
```

Per-user install without the service: the same commands without `sudo`. Keep
using the same form for every later command, so they all share one config dir.

## Enroll

**Registration token:**

```bash
sudo meteorcli register --server http://localhost:8000 --token-file /tmp/meteorcli.token --name edge-01
# per-user install without the service:
meteorcli register --server http://localhost:8000 --token-file /tmp/meteorcli.token --name edge-01
```

**Device-initiated:**

```bash
sudo meteorcli request-token --name edge-01
# per-user install without the service:
meteorcli request-token --name edge-01
```

With `meteorcli.service` running, `request-token` returns right away and the
service waits for the approval (every 10 seconds, across reboots; an expired
request is resubmitted with the stored API key), then connects. Follow it with
`journalctl -u meteorcli -f`. Pass `--wait SECONDS` to wait in the terminal
instead. A rejected request sends the service back to waiting for a new one.
`request-token` waits in the terminal itself when the service is already
connected (re-enrolling) or `--server` differs from the saved server, since the
service would not collect that request.

Without the service, `request-token` waits up to ~5 minutes (`--wait`); after a
later approval run `meteorcli claim`, then start the agent in the foreground:

```bash
meteorcli status
meteorcli run                           # foreground; Ctrl+C to stop
meteorcli run --once                    # one heartbeat (allowed even if the service is running)
```

`meteorcli run` is not a daemon. If `meteorcli.service` is already active, it exits instead of starting a second copy. Stop the unit first (`sudo systemctl stop meteorcli`) when you need a foreground session.

Foreground `meteorcli run` is unprivileged. systemd Deploys go through
`meteorcli-systemd` (installed by `sudo ./installcli.sh`). `meteorcli.service`
already runs as root and does not need that helper.

## Related

- [Getting started](getting-started.md)
- [Registration tokens](fleet/registration-tokens.md)
- [Device-initiated enrollment](fleet/device-request-enrollment.md)
- [First-boot enroll on a Pi image](../src/device-plane/agent/packaging/firstboot/README.md) (`install-meteorcli-firstboot.sh`; build your own OS image with `meteorcli-prepare-image.sh`)
- [Heartbeat](fleet/heartbeat.md)
- [MQTT](fleet/mqtt.md)
