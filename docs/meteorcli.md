# meteorcli

Device-plane CLI. Runs on managed Linux devices: configure the control plane, enroll, heartbeat, and keep an MQTT session.

Package details and install script: [`src/device-plane/agent/README.md`](../src/device-plane/agent/README.md).

## Install

**Today** the installer `pip install`s the agent directory. It also needs sibling
`src/device-plane/runtime/` (systemd/docker/compose adapters). A device that
only wants `meteorcli` still needs those two trees (or a clone of this repo).

```bash
cd src/device-plane/agent
./installcli.sh          # current user (~/.local)
sudo ./installcli.sh     # system-wide
meteorcli --help
```

**Target (not implemented):** a GitHub Release (`agent-v*`) with
`meteorcli-<version>.tar.gz` (agent + `runtime` + `installcli.sh` only, no
secrets) and `install-meteorcli.sh`, so devices never clone the cloud. Remaining
work: pack script, remote install mode, CI on tag, console one-liner. See
[Architecture → Device agent](architecture.md#device-agent-meteorcli).

Config directory: `~/.config/meteorcli` (or `/etc/meteorcli` as root).

## Commands

| Command | Purpose |
| --- | --- |
| `config` | Store control-plane domain / API base and enrollment API key |
| `test` | Reachability + API key check |
| `register` | Enroll with a registration token |
| `request-token` | Device-initiated enroll; poll for approval |
| `claim` | Fetch device token after a later approval |
| `run` | Heartbeats + MQTT loop (`--once` for a single pass). Run as the device user; WireGuard uses passwordless sudo for `wg` / `wg-quick` only |
| `status` | Show non-secret local config |
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

Then:

```bash
meteorcli run
meteorcli run --once
```

## Related

- [Getting started](getting-started.md)
- [Registration tokens](fleet/registration-tokens.md)
- [Device-initiated enrollment](fleet/device-request-enrollment.md)
- [Heartbeat](fleet/heartbeat.md)
- [MQTT](fleet/mqtt.md)
