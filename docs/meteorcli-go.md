# meteorcli in Go (in progress)

The device agent is being rewritten in Go in [`src/device-plane/agent-go/`](../src/device-plane/agent-go/).
It speaks exactly the same contract as the Python agent
([`contracts/device-agent-v1.md`](../contracts/device-agent-v1.md)); the control plane, console and
data plane do not change. Until it is complete, devices keep installing the Python `meteorcli`
([meteorcli](meteorcli.md)). Nothing installs the Go binary yet.

## What exists today

| Part | State |
| --- | --- |
| Core: modules, supervisor, crash-loop breaker, health, signals, systemd notify and watchdog, log redaction | Done |
| Config: new file layout, import of a Python install | Done |
| State: versioned state files, persistent outbox | Done |
| API client: health, enrollment, register, heartbeat, MQTT settings | Done (the other routes come with their modules) |
| CLI: `config`, `config import`, `status`, `--version`, `agent` (alias `run`) | Done |
| Modules: identity, heartbeat, MQTT bus, actions, deploy, network, telemetry, update | Next pull requests |
| Agent socket, root helper, updater, installer and systemd units | Next pull requests |

## Files

| Path | Owner, mode | Holds |
| --- | --- | --- |
| `/etc/meteorcli/config.json` | root:meteor 0640 | Server (`domain`, `api_base`, `server_url`), optional pinned CA (`ca_file`). Written by root, read by the agent. |
| `/var/lib/meteorcli/identity/` | meteor 0700, files 0600 | `device.json` (device id, organization, name, heartbeat interval), `device-token`, `mqtt.json`, `mqtt-ca.crt`, `api-key`, `pending-claim.json`, `wireguard/` keys |
| `/var/lib/meteorcli/state/` | meteor 0700 | Versioned module state (`signing-keys.json`, `applied-software.json`, ...) and `outbox/` |
| `/var/lib/meteorcli/artifacts/` | meteor 0750 | Downloaded release artifacts |
| `/run/meteorcli/` | meteor:meteorcli 0750 | `agent.sock`, `helper.sock` |

Every JSON file has a `version` field and keeps keys this version does not know. Files are written
atomically (temp file, fsync, rename). A file that does not parse is moved to
`<name>.corrupt-<unix time>` and the agent starts that part clean.

`METEORCLI_ROOT=/some/dir` (or `--root`) moves every path under a prefix, for tests and development.

## Moving a device from the Python agent

The Python agent kept settings and secrets together in `/etc/meteorcli`. Stop it first: the Python
service rotates the device token and MQTT password, and a rotation between the copy and the clean-up
would be lost. As root:

```bash
sudo systemctl stop meteorcli           # the Python service
sudo meteorcli config import            # reads /etc/meteorcli
sudo meteorcli config import --from ~/.config/meteorcli --force
meteorcli status
```

The import refuses when the new config already points at a different server than the Python
install (enrollment files belong to one server); `--force` replaces it. If a token is lost anyway,
enroll again: `meteorcli config --api-key …` and request a token as on a new device.

The import:

- keeps the server settings in `/etc/meteorcli/config.json` (adds `version: 1`, keeps unknown keys)
  and saves the old file as `config.json.python`;
- moves `device_id`, `organization_id`, `name` and `heartbeat_interval_seconds` to `identity/device.json`;
- moves `device-token`, `api-key`, `mqtt.json` (its `ca_cert_path` is rewritten), `mqtt-ca.crt`,
  `claim-secret` (as `pending-claim.json`) and the WireGuard key pair to `identity/`;
- moves `signing-keys.json` and `applied-software.json` to `state/`;
- uses a finished `.device-secrets-staging` journal (one with `DONE`) and drops an unfinished one;
- removes the old copies only after every new file is written, and skips files that already exist
  unless `--force`. Running it again does nothing.

Not imported: `artifacts/`, `app-credentials/`, `compose/`, `agent-update*.json` and `wg0.conf`.
The modules that own them recreate them.

`status`, `config` and `agent` point at `meteorcli config import` while a Python layout is in place.

## Commands

| Command | Same as Python | Notes |
| --- | --- | --- |
| `meteorcli config --domain HOST [--api-base URL] [--http] [--api-key KEY]` | Yes | Same URL rules (IPs and localhost use HTTP, `api.<ip>` is stripped). Changing the server removes the old server's credentials. Needs root. |
| `meteorcli config --show` | Yes | Never prints secrets. |
| `meteorcli config import [--from DIR] [--force]` | New | See above. |
| `meteorcli status` | Yes, same lines | Checks secret files by `stat` only and never reads them. Exits 1 until enrolled. |
| `meteorcli -V`, `--version` | Yes | |
| `meteorcli agent` (alias `run`) | Service entry point | Runs until SIGTERM. Keeps running when not configured. |

Until the agent socket exists, `status` reads `device.json` directly, so the enrollment lines need
root (or the meteor user). Other users see `unknown (permission denied; use sudo)`.

## How the core works

- **Modules** implement `Name()`, `Critical()` and `Run(ctx, Deps)` and are listed in
  `internal/agent/modules.go`. They never import each other. They cooperate through the signals hub
  (`Enrolled`, `WorkWaiting{Kind}`, `CredentialsRotated`, `ConfigChanged`: never blocks, latest wins per
  key) and the outbox (a report is removed only after the server accepts it).
- **Supervisor**: one goroutine per module. A panic becomes an error, health shows `failed`, and the
  module restarts after 1 s, doubling to 5 min. The delay resets after 10 healthy minutes.
- **Crash-loop breaker**: 5 failures in 10 min park an optional module (`parked` in
  `capabilities.modules`). It runs again on `ConfigChanged`, `Supervisor.Restart` (for
  `meteorcli module restart` and the `restart_module` action), a new agent version (the process
  restarts), or after 1 hour. Critical modules (identity, heartbeat, MQTT) are never parked.
- **systemd**: `READY=1` once the modules started, `WATCHDOG=1` only while every running critical
  module called `Health.Tick` in the last 3 minutes, `STOPPING=1` on shutdown.
- **Logs**: text lines for journald, every line has `module=<name>`. Tokens, passwords, PSKs, API keys
  and `Authorization` values are redacted by attribute name and by pattern.

## Build and test

```bash
make agent-go-test     # gofmt, go vet, go test -race
make agent-go-build    # static linux arm64, arm v6, amd64 binaries in src/device-plane/agent-go/bin
```

CI (`Agent (Go meteorcli)`) also runs staticcheck, govulncheck, a coverage floor of 70% on the core
packages, and fails when a binary is over 15 MB. Today's binaries are about 6.5 MB and the idle agent
uses about 11 MB RSS.

The API client tests replay the Python agent's recorded exchanges in
[`tests/agent-contract/recordings/`](../tests/agent-contract/recordings/): same routes, headers,
body keys, and the answers decode.

No third-party Go modules yet: the module uses the standard library only. staticcheck (MIT) and
govulncheck (BSD-3-Clause) run in CI and are not shipped.
