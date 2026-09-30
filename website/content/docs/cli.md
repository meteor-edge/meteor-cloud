---
title: CLI
description: meteorcli commands and environment variables.
section: Reference
order: 22
---

```bash
meteorcli --help
meteorcli <command> --help
meteorcli --version
```

| Command | Purpose |
| --- | --- |
| `config` | Store control-plane domain and API key |
| `test` | Reachability and API key check |
| `register` | Enroll with a one-time registration token |
| `request-token` | Ask for a device token; poll briefly for approval |
| `claim` | Collect the token after a later approval |
| `run` | Heartbeats and MQTT (`--once` for a single pass) |
| `mqtt-test` | Publish one TLS MQTT message (default status) |
| `mqtt-listen` | Print inbound command payloads |
| `status` | Show persisted, non-secret configuration |

Environment: `METEORCLI_DOMAIN`, `METEORCLI_SERVER`, `METEORCLI_API_KEY`, `METEORCLI_TOKEN`, `METEORCLI_CONFIG_DIR`.

Secrets are never printed by `status` or `config --show`. The API key file is mode `0600`.

### Configure

```bash
meteorcli config --domain your-api.example.com --api-key key_...
meteorcli test
```

Local HTTP:

```bash
meteorcli config --domain 127.0.0.1:8000 --api-key key_...
```

### Register (admin token)

```bash
printf '%s' "reg_..." > /tmp/meteorcli.token
meteorcli register --token-file /tmp/meteorcli.token --name edge-01
```

### Request a device token

```bash
meteorcli request-token --name edge-01
meteorcli claim    # if approval happened later
meteorcli run
```

`request-token` polls for at most five minutes (`--wait`). It will not poll faster than every 10 seconds. Use `--new` only to replace a still-pending request.

### MQTT helpers

```bash
meteorcli mqtt-test
meteorcli mqtt-listen
```

The device must already have `mqtt.json` from register/claim. `MQTT_PUBLIC_HOST` on the server must be reachable from the device (not `localhost` for a Pi on the LAN).
