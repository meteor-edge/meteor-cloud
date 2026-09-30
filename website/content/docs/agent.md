---
title: Device agent
description: Install meteorcli and enroll a Linux device.
section: Reference
order: 21
---

`meteorcli` runs on the managed Linux machine. It enrolls with the control plane over HTTPS, sends heartbeats, and keeps a TLS MQTT session.

## Install

From `device-plane/agent` on the device:

```bash
./installcli.sh          # current user (~/.local)
sudo ./installcli.sh     # system-wide
meteorcli --help
```

User install: venv in `~/.local/share/meteorcli`, binary in `~/.local/bin`. System: `/opt/meteorcli` and `/usr/local/bin`. Config is `~/.config/meteorcli` or `/etc/meteorcli` as root. Uninstall: `./installcli.sh --uninstall` (credentials kept).

Development: `python -m pip install -e ".[dev]"` from that directory.

## Two enrollment paths

1. **Admin token** — create a registration token in the console, then `meteorcli register`.
2. **Request / approve / claim** — configure an organization API key, `meteorcli request-token`, approve in the console, then `meteorcli claim` if needed.

`--domain` is the API host (no `api.` subdomain). `example.com` becomes `https://example.com`. An IP or localhost uses HTTP. Override with `--api-base` or `METEORCLI_SERVER`.

After enrollment, `meteorcli run` heartbeats and MQTT. See [CLI](/docs/cli) for every command.
