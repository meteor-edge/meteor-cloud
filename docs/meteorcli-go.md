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
| CLI: `config`, `config import`, `status`, `module restart`, `helper`, `--version`, `agent` (alias `run`) | Done |
| Agent socket with a group policy per command; root helper (`power.*`, `logs.read`, `systemd.install_unit`) | Done |
| systemd units, sysusers.d, tmpfiles.d in `packaging/` | Done (nothing installs them yet) |
| Modules: identity, heartbeat, MQTT bus, actions, deploy, network, telemetry, update | Next pull requests |
| Updater, installer | Next pull requests |

## Files

| Path | Owner, mode | Holds |
| --- | --- | --- |
| `/etc/meteorcli/config.json` | root:meteor 0640 | Server (`domain`, `api_base`, `server_url`), optional pinned CA (`ca_file`). Written by root, read by the agent. |
| `/var/lib/meteorcli/identity/` | meteor 0700, files 0600 | `device.json` (device id, organization, name, heartbeat interval), `device-token`, `mqtt.json`, `mqtt-ca.crt`, `api-key`, `pending-claim.json`, `wireguard/` keys |
| `/var/lib/meteorcli/state/` | meteor 0700 | Versioned module state (`signing-keys.json`, `applied-software.json`, ...) and `outbox/` |
| `/var/lib/meteorcli/artifacts/` | meteor 0750 | Downloaded release artifacts |
| `/run/meteorcli/` | meteor:meteorcli 0750 | `agent.sock` (meteor:meteorcli 0660), `helper.sock` (root:meteor 0660) |

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
| `meteorcli status` | Yes, same lines | Asks the running agent over `agent.sock` (adds an `Agent:` line and one line per module). When the agent is not running it reads the files and checks secrets by `stat` only. Never shows secrets. Exits 1 until enrolled. |
| `meteorcli module restart NAME` | New | Restarts (or unparks) one module of the running agent. Needs `meteorcli-admin`. |
| `meteorcli helper` | Service entry point | The root helper behind `meteorcli-helper.socket`. |
| `meteorcli helper call OP [JSON]` | New | Sends one request to the helper; only the meteor user is served. For debugging and tests. |
| `meteorcli -V`, `--version` | Yes | |
| `meteorcli agent` (alias `run`) | Service entry point | Runs until SIGTERM. Keeps running when not configured. |

When the agent is not running, `status` reads `device.json` directly, so the enrollment lines need
root (or the meteor user). Other users see `unknown (permission denied; use sudo)`.

## Who may do what

The agent runs as the unprivileged `meteor` user. Local users talk to it over
`/run/meteorcli/agent.sock`; the agent asks the kernel who is calling (`SO_PEERCRED`) and checks a
policy per command (`socketPolicy` in `internal/agent/socket.go`):

| Group | May run |
| --- | --- |
| `meteorcli` | `status`, `logs`, `test`, `doctor`, `mqtt-listen` |
| `meteorcli-admin` | the above, plus `register`, `request-token`, `claim`, `config set`, `update`, `module restart` |
| root | everything |

Today the socket serves `status` and `module restart`; the other commands arrive with their modules.
`agent.sock` belongs to the `meteorcli` group, so an administrator is in **both** groups:

```bash
sudo usermod -aG meteorcli,meteorcli-admin alice   # then log in again
```

Every request is logged to the journal with the caller's uid and pid, the method, the outcome and
a few named fields (a module or unit name); raw request parameters are never logged. Answers never
contain secrets (status says `present`/`missing`). The socket serves at most 16 connections at once,
at most 4 from one user (root has a few extra slots), and gives a client 5 seconds to send its
request, so one user holding connections open locks out neither other users nor root (the
container test checks this). Four or more members of the group acting together can keep all 16
slots full by reopening connections, which blocks `status` and `module restart` for everyone but
root while they do; only add trusted local accounts to `meteorcli`.

The agent holds an `flock` on `agent.sock.lock` while it serves `agent.sock`. A second agent with
the same paths refuses to start instead of replacing the live socket; after a crash the kernel drops
the lock and the next start replaces the stale socket. As a second guard the agent removes an old
socket only when connecting to it is refused; a socket that answers, or one it cannot check, is
left alone and the agent fails to start (stop the other process first).

### Root helper

The few privileged operations go through `meteorcli helper`, a root service started on demand by
`meteorcli-helper.socket` (`/run/meteorcli/helper.sock`, root:meteor 0660). It serves only the uid
of the `meteor` user (root and other members of the `meteor` group are refused), runs only the
operations in `internal/helper/ops.go` with typed, validated parameters (unknown fields are
rejected), runs tools with an argument list and a fixed `PATH`, logs every request, and exits after
a minute without requests.

| Operation | Params | Does |
| --- | --- | --- |
| `power.reboot`, `power.shutdown` | none | `systemctl --no-block reboot` / `poweroff` (answers first) |
| `logs.read` | `source` (`journal` or `systemd`), `unit`, `lines` (1-10000, default 200), `since` (`15m`, `1h`, `7d`), `grep` (up to 200 printable characters) | `journalctl` with fixed options; returns `lines` |
| `systemd.install_unit` | `name` (`^[a-z0-9][a-z0-9._-]{0,62}$`, ending `.service`, never `meteorcli*`), `content` (needs `[Service]`, at most 256 KiB), `no_start` | Atomic write to `/etc/systemd/system`, `daemon-reload`, `enable`, `restart` |

**Trust boundary.** `systemd.install_unit` checks the unit's name and shape, not what `ExecStart`
runs, so whoever controls the `meteor` user can run commands as root through it (as with Docker on a
device). The `meteor` account is unprivileged, not confined: protect it like root. The agent only
installs units from releases it verified.

Tools (`systemctl`, `journalctl`) are looked up only in a fixed root-owned `PATH`, never in the
helper's environment. `logs.read` streams `journalctl` output into a buffer of the requested
number of matching lines. `grep` searches every line in full (ignoring ASCII case); a line longer
than 16 KiB is returned cut, ending in `…[truncated N bytes]`.

Docker and file logs, `systemd.control`, users and Wi-Fi follow the Python helpers in later pull
requests.

### Packaging

[`src/device-plane/agent-go/packaging/`](../src/device-plane/agent-go/packaging/) holds
`meteorcli.service`, `meteorcli-helper.service`, `meteorcli-helper.socket`,
`sysusers.d/meteorcli.conf` (user `meteor`, groups `meteorcli` and `meteorcli-admin`) and
`tmpfiles.d/meteorcli.conf` (the directories above).

- `meteorcli.service`: `Type=notify`, `WatchdogSec=60`, `User=meteor`, no capabilities,
  `ProtectSystem=strict`, `SystemCallFilter=@system-service`, `MemoryMax=128M`, `Restart=always`.
  `systemd-analyze security` exposure 1.6.
- `meteorcli-helper.service`: root with a limited capability set, `NoNewPrivileges`, `PrivateTmp`,
  `ProtectKernelModules`, `ProtectHome=read-only` and a system call filter. Exposure 3.0.
- `/run/meteorcli` comes from tmpfiles.d instead of `RuntimeDirectory=`: systemd would delete it,
  with `helper.sock`, whenever the agent stops, and would give it to `meteor:meteor`, locking the
  `meteorcli` group out. The agent has `SupplementaryGroups=meteorcli` so it can give `agent.sock` to
  that group without a capability.

CI fails when the agent's exposure reaches 2.0 or the helper's reaches 6.0.

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
packages, the `systemd-analyze security` gate, and the systemd container test, and fails when a
binary is over 15 MB.

```bash
make agent-go-systemd-test   # Docker, --privileged: Debian 12 with systemd as PID 1
```

The container test installs the units, sysusers and tmpfiles files, creates test users, and checks:
a `meteorcli-admin` user runs `status` and `module restart` without sudo, a `meteorcli` user may run
`status` but not `module restart`, a user in neither group is refused, the helper refuses every uid
except `meteor` (also root, and a user in the `meteor` group), `logs.read` and
`systemd.install_unit` work for `meteor`, the audit lines are in the journal, and no token is
logged. Today's binaries are about 6.5 MB and the idle agent
uses about 11 MB RSS.

The API client tests replay the Python agent's recorded exchanges in
[`tests/agent-contract/recordings/`](../tests/agent-contract/recordings/): same routes, headers,
body keys, and the answers decode.

No third-party Go modules yet: the module uses the standard library only. staticcheck (MIT) and
govulncheck (BSD-3-Clause) run in CI and are not shipped; neither is the test-only `debian:12.8-slim`
image ([licensing](licensing.md)).
