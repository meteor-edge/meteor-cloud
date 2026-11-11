# meteorcli (device agent)

`meteorcli` is the MeteorCloud device command. It runs on managed Linux devices,
registers them with the control plane, sends heartbeats, and maintains a TLS MQTT
session (`paho-mqtt`).

There are **two ways** to enroll a device:

1. An administrator creates a registration token; the device runs `meteorcli register`.
2. The device is configured with an organization API key and runs
   `meteorcli request-token`; an administrator approves the pending request; the CLI
   polls for a limited time (or later `meteorcli claim`) and stores the device token.

## Install

**Supported device OS for fleets today:** Raspberry Pi OS (Bookworm). Other
Debian-like systems work the same way.

**On a device** (no cloud clone):

```bash
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | sudo bash
```

Maintainers publish that file by tagging `agent-v<version>` (see [meteorcli](../../../docs/meteorcli.md)).

From this directory (also requires sibling `../runtime`, included in the wheel
via Hatch `force-include`):

```bash
./installcli.sh                    # current user (~/.local); no background service
sudo ./installcli.sh               # system-wide + start meteorcli.service
sudo ./installcli.sh --no-service  # system-wide, CLI only
meteorcli --help
```

A root install starts `meteorcli.service` when systemd is running. Before the
device is registered the service only waits; once a token is stored or an
enrollment request is approved it connects (heartbeats, MQTT, WireGuard).
`--with-service` is still accepted and turns "no systemd" into an error.

A user install puts the venv in `~/.local/share/meteorcli` and the command in
`~/.local/bin`. A system install uses `/opt/meteorcli` and `/usr/local/bin`.
Re-run the script to upgrade. Uninstall with `./installcli.sh --uninstall`
(removes the venv, systemd unit, `~/.config/meteorcli` or `/etc/meteorcli`,
and brings `wg0` down when the helper is present).

`meteorcli run` downloads desired artifacts and applies **systemd**, **docker**,
and **docker_compose** releases. Run it as the device user (not `sudo meteorcli`).
`sudo ./installcli.sh` installs `wireguard-tools` when needed, plus root-owned
helpers and passwordless sudo for those files only, so `meteorcli run` can stay
unprivileged:

- `/usr/local/libexec/meteorcli-wg` — create `wg0` (meteorcli's own `wg0.conf`
  keys only; no `PostUp`/`PreUp` hooks)
- `/usr/local/libexec/meteorcli-logs` — read-only logs for the console Logs
  window and `meteorcli logs` (see [Device logs](../../../docs/fleet/logs.md))
- `/usr/local/libexec/meteorcli-systemd` — install a Deployed `.service` unit
  under `/etc/systemd/system` (does not overwrite `meteorcli.service`)

When the control plane has WireGuard enabled, `run` registers this device’s
**public** key and brings up a tunnel (private key stays on the device). See
[Device connectivity](../../../docs/fleet/connectivity.md).

Config and credentials go in `/etc/meteorcli` whenever `meteorcli.service` is
installed (for every user, so enrollment lands where the service reads it; use
`sudo meteorcli …`). Without the service: `~/.config/meteorcli` for a normal
user, `/etc/meteorcli` for root. Re-running `sudo ./installcli.sh` copies an
existing user config into `/etc/meteorcli` when that dir has no `device-token` yet.

**Golden image / first boot:**

```bash
curl -fsSL https://raw.githubusercontent.com/meteor-edge/meteor-cloud/master/src/device-plane/agent/packaging/firstboot/install-meteorcli-firstboot.sh | sudo bash
```

See [`packaging/firstboot/`](packaging/firstboot/README.md). First-boot installs
latest `meteorcli`, runs `request-token`, and polls with `claim` in
`meteorcli-firstboot.service` until an administrator approves, then starts
`meteorcli.service` and removes first-boot. The SSH session does not wait for
approval.

For local development:

```bash
python -m pip install -e ".[dev]"
```

This also installs the `edge-agent` internal alias.

## Usage

```bash
meteorcli --help
meteorcli <command> --help
meteorcli --version
```

| Command          | Purpose                                                              |
| ---------------- | -------------------------------------------------------------------- |
| `config`         | Store the control-plane domain and API key.                          |
| `test`           | Check that the server is reachable and the API key is valid.         |
| `register`       | Enroll with a one-time registration token (admin-initiated).         |
| `request-token`  | Request a device token to connect this machine to the API.           |
| `claim`          | Collect the device token after a later approval.                     |
| `run`            | Foreground heartbeats, MQTT, WireGuard (refuses if `meteorcli.service` is already active). |
| `status`         | Show persisted, non-secret configuration, systemd, and WireGuard.    |

Environment variables: `METEORCLI_DOMAIN`, `METEORCLI_SERVER`, `METEORCLI_API_KEY`,
`METEORCLI_TOKEN`, `METEORCLI_CONFIG_DIR` (default: `~/.config/meteorcli`, or
`/etc/meteorcli` as root).

The value you pass to `--domain` is the API host. There is no `api.` subdomain.
`meteorxx.com` becomes `https://meteorxx.com`. An IP or localhost uses HTTP on
that address (`http://192.168.0.107:8000`). Pass `--http` to force HTTP for a
name, or `--api-base` / `METEORCLI_SERVER` to set the origin explicitly.

## Configure the CLI

```bash
meteorcli config --domain meteorxx.com --api-key key_...
meteorcli test
meteorcli config --show
```

Local / HTTP testing:

```bash
meteorcli config --domain 192.168.0.107:8000 --api-key key_...
meteorcli test
# or: meteorcli config --api-base http://192.168.0.107:8000 --api-key key_...
```

The API key is stored at `~/.config/meteorcli/api-key` (or `/etc/meteorcli/api-key`
as root) with `0600` permissions and is never printed by `status` or `--show`.

## Path 1 — register with a token

Create a token in the dashboard (**Devices → Add device**). Prefer a token file:

```bash
printf '%s' "reg_..." > /tmp/meteorcli.token
meteorcli register --token-file /tmp/meteorcli.token --name edge-01
```

Or pass `--server` / `--token` explicitly if the CLI is not yet configured.

## Path 2 — request a device token

```bash
meteorcli request-token --name edge-01
```

The command asks the API for a device token, then polls for at most five minutes
(override with `--wait`). It does not wait forever and will not poll faster than
every 10 seconds. If the request is still pending, the claim secret stays on disk.

When an administrator approves later (even the next day), collect the
token without submitting a new request:

```bash
meteorcli claim
```

`meteorcli request-token` will refuse to create a second request while a claim is
still pending on the server; pass `--new` only if you intend to replace it.
After a rejection or expiry, run `request-token` again (no `--new` needed).
Without the service, use `meteorcli run` after the token is stored.

With `meteorcli.service` running, `request-token` does not wait (unless you pass
`--wait`): the service waits for approval (every 10 seconds, also across
reboots), then starts heartbeats. If the request expires, it submits a new one
with the stored API key. A rejected request sends the service back to waiting.
Before anything is configured the service also just waits, so the installer can
start it right away. A foreground `meteorcli run` with a pending request waits
the same way; there a rejection ends the command. Meteor Imager's `meteor-firstrun.sh` uses this:
it sends the request, deletes itself and `.meteorenv` from the boot partition,
and starts `meteorcli.service`.

## Send heartbeats

`meteorcli run` is a foreground loop (heartbeats, MQTT, WireGuard). It stays in
the terminal until Ctrl+C. It is not a background daemon.

```bash
systemctl status meteorcli              # started by a root install
journalctl -u meteorcli -f
meteorcli status
meteorcli run                           # foreground; refused if the service is already active
meteorcli run --once                    # one pass (allowed even if the service is running)
```

If `meteorcli.service` is already running, a second `meteorcli run` would
duplicate the agent. Stop it first (`sudo systemctl stop meteorcli`) when you
need a foreground session.

## Testing

```bash
python -m pytest -q
```

Paths are injectable via `--config-dir`, so tests never touch `/etc/meteorcli`.
