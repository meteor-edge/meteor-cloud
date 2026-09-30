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

On a Linux device, from this directory:

```bash
./installcli.sh          # current user (~/.local)
sudo ./installcli.sh     # system-wide
meteorcli --help
```

A user install puts the venv in `~/.local/share/meteorcli` and the command in
`~/.local/bin`. A system install uses `/opt/meteorcli` and `/usr/local/bin`.
Re-run the script to upgrade. Uninstall with `./installcli.sh --uninstall`
(credentials are kept).

Config and credentials go in `~/.config/meteorcli` for a normal user, or
`/etc/meteorcli` when running as root. You do not need root to run
`meteorcli config`, `test`, or `request-token` as your own user.

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
| `run`            | Send heartbeats and keep an MQTT session (loop, or `--once`).     |
| `status`         | Show persisted, non-secret configuration.                            |

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
Use `meteorcli run` after the token is stored.

## Send heartbeats

```bash
meteorcli run
meteorcli run --once
```

## Testing

```bash
python -m pytest -q
```

Paths are injectable via `--config-dir`, so tests never touch `/etc/meteorcli`.
