# meteorcli internals (Go)

The device agent is written in Go in [`src/device-plane/agent-go/`](../src/device-plane/agent-go/)
and speaks [`contracts/device-agent-v1.md`](../contracts/device-agent-v1.md). From 0.5.0 the
`agent-v*` releases (`releases/latest`) are the Go agent; installing, updating and first boot for
operators are in [meteorcli](meteorcli.md). This page covers the internals.

## What exists today

| Part | State |
| --- | --- |
| Core: modules, supervisor, crash-loop breaker, health, signals, systemd notify and watchdog, log redaction | Done |
| Config: new file layout | Done |
| State: versioned state files, persistent outbox | Done |
| API client: health, enrollment, register, heartbeat, MQTT settings, actions, software, signing keys, artifact downloads, application credentials | Done (the other routes come with their modules) |
| CLI: `config`, `config meteorenv`, `status`, `register`, `request-token`, `claim`, `test`, `module restart`, `logs`, `mqtt-test`, `mqtt-listen`, `helper`, `--version`, `agent` (alias `run`) | Done |
| Agent socket with a group policy per command; root helper (`ping`, `power.*`, `logs.*`, `systemd.install_unit`, `systemd.control`, `users.*`, `docker.*`, `compose.*`, `wifi.*`, `wireguard.*`, `ssh.authorized_keys`) | Done |
| systemd units, sysusers.d, tmpfiles.d in `packaging/` | Done |
| Installers (`install-meteorcli.sh` for any board, `install-rpi.sh`), first-boot and OS image scripts, CI artifact, `agent-v*` release, `meteorcli-go-edge` prerelease | Done |
| Identity: register, request-token and claim run by the agent, background approval polling, credential storage, MQTT credential refresh, 401 handling | Done |
| Heartbeat module; telemetry (inventory, metrics); platform adapters (Raspberry Pi, Jetson, generic Linux) | Done |
| MQTT: `bus` (one session, TLS with the stored CA, reconnect with backoff), `mqtt` module (status, ping, `sync_desired`), `metrics` module (60 s metrics, app and journal events) | Done |
| Actions: `reboot`, `shutdown`, `set_password`, `add_user` through the helper | Done |
| Deploy: signed desired software, streamed artifact downloads with SHA-256, `systemd`, `docker` and `docker_compose` runtimes through the helper, health gate and rollback | Done |
| `appcreds` module: SDK tokens of bound workloads rotated in place | Done |
| Network: `wifi` module (changes with commit-confirm, status reports), `wireguard` module (device tunnel, SSH keys for the browser terminal); `logs` for the console's Logs window | Done |
| Updater: `update_agent` and `meteorcli update` start `meteorcli updater` (signed release manifest, SHA-256, `meteorcli.previous`, heartbeat health gate, automatic rollback); the release jobs sign the manifest | Done |

## Files

| Path | Owner, mode | Holds |
| --- | --- | --- |
| `/etc/meteorcli/config.json` | root:meteor 0640 | Server (`domain`, `api_base`, `server_url`), optional pinned CA (`ca_file`), `enroll_name` (from a meteorenv file), `ssh_user` (the account the console's browser terminal signs in as; the installer sets the user who ran it), `release_url` (where the updater reads releases; empty for the newest `agent-v*` release). Written by root, read by the agent, the helper and the updater. |
| `/var/lib/meteorcli/identity/` | meteor 0700, files 0600 | `device.json` (device id, organization, name, heartbeat interval), `device-token`, `mqtt.json`, `mqtt-ca.crt`, `api-key` (until enrolled), `pending-claim.json` (request id and claim secret while waiting), `wireguard/` keys. Written by the agent only, except `config` (root) storing an API key and removing a server's files. |
| `/var/lib/meteorcli/state/` | meteor 0700 | Versioned module state (`signing-keys.json`, `applied-software.json`, `wireguard.json` (the last tunnel settings, no private key), `agent-update.json` (an `update_agent` action waiting for the updater), `agent-update-outcome.json` (what the last update did; written by the updater as root, handed to meteor), ...) and `outbox/` |
| `/usr/local/bin/meteorcli.previous` | root 0755 | The binary the last update replaced (a hard link); the updater puts it back when the new one does not come up |
| `/var/lib/meteorcli/artifacts/` | meteor 0750 | Downloaded release artifacts (`<artifact id>` and `<artifact id>.sha256`) |
| `/var/lib/meteorcli/compose/` | meteor 0700 | One directory per Compose project (0755): the compose file or unpacked bundle, `.env.meteorcloud` (0600) and `compose.meteorcloud.json` (0600) |
| `/var/lib/meteorcli/app-credentials/` | meteor 0700 | Application tokens of bound workloads, one directory per workload: `token` (0644, mounted read-only into the container) and `meta.json` (0600) |
| `/etc/meteorcli/docker/` | root, optional | Docker registry credentials for deploys (`sudo docker --config /etc/meteorcli/docker login …`); the helper runs docker with `DOCKER_CONFIG` pointing here |
| `/var/lib/meteorcli-helper/` | root 0700 (`StateDirectory=`) | The helper's own state: `wifi/pending.json` and `wifi/backup/` (a Wi-Fi change waiting for confirm and the profiles it replaced), `wireguard/wg0.conf` (0600, holds the private key) |
| `/usr/local/libexec/meteorcli-logs`, `/etc/sudoers.d/meteorcli-logs` | root 0755 / 0440 | The path the console's Logs window runs over SSH (`exec meteorcli logs "$@"`), allowed with `sudo -n` for the `meteorcli` group |
| `/run/meteorcli/` | meteor:meteorcli 0750 | `agent.sock` (meteor:meteorcli 0660), `helper.sock` (root:meteor 0660) |

Every JSON file has a `version` field and keeps keys this version does not know. Files are written
atomically (temp file, fsync, rename). A file that does not parse is moved to
`<name>.corrupt-<unix time>` and the agent starts that part clean.

`METEORCLI_ROOT=/some/dir` (or `--root`) moves every path under a prefix, for tests and development.

## Installing on a device

[meteorcli: Install](meteorcli.md#install) has the commands. Releases:

- **`agent-v<version>`** (workflow `meteorcli release`, from `src/device-plane/agent-go/VERSION`):
  the "latest" release that devices install and update from.
- **`meteorcli-go-edge`** (workflow `meteorcli (Go) edge build`): a prerelease rebuilt from every
  merge to master that touches the agent.

Both hold the binaries, the signed manifest, the packaging files, `install-meteorcli.sh` (any
board), `install-rpi.sh` (Raspberry Pi only; `--force` installs on another board), the first-boot
scripts and `SHA256SUMS`. The installers are one script (`packaging/install/install.sh`);
`scripts/dist.sh` fills in the board and the release URL.

The installer:
- checks the board (`install-rpi.sh` only) and picks the binary by `uname -m`
- downloads and verifies against `SHA256SUMS`
- installs `/usr/local/bin/meteorcli`, the units, sysusers.d and tmpfiles.d files
- adds `$SUDO_USER` (or `--user`) to `meteorcli` and `meteorcli-admin`
- applies `--domain`/`--api-base`/`--api-key`, starts the agent and runs `register` when given a token

Running it again upgrades in place. A device still on the Python agent (0.4.x, removed from
this repository) cannot be moved with its identity: wipe it and enroll it again.

To try a pull request before it is merged:
1. Download the `meteorcli-go-<sha>` artifact from the CI run's Summary page and unzip it on the device.
2. Run `sudo bash install-meteorcli.sh` in that folder.

`make agent-go-dist` builds the same files locally (on a laptop or on the device itself, with Go
1.24+), versioned `0.0.0-dev+<commit>`. Run as a file, the installer installs the files next to
it when `SHA256SUMS` is there; piped from curl, it downloads the release it came from.
`--base-url` overrides both, e.g. a laptop serving `dist/` with `python3 -m http.server`.
`meteorcli --version` shows which build is installed.

Once installed, a device updates itself from the console (**Update meteorcli**) or with
`meteorcli update`; see [Updating](#updating). Running the installer again also upgrades.

## Commands

| Command | Same as Python | Notes |
| --- | --- | --- |
| `meteorcli config --domain HOST [--api-base URL] [--http] [--api-key KEY] [--enroll-name NAME] [--ssh-user NAME] [--release-url URL]` | Yes | Same URL rules (IPs and localhost use HTTP, `api.<ip>` is stripped). Changing the server removes the old server's credentials. `--enroll-name` is the device name asked for on approval enrollment (the installer passes `--name` as this). `--ssh-user` is new (Python used the user that ran the installer). `--release-url` (`https://`, `http://` or `file://`; `default` resets it) is where the updater reads releases. Needs root. |
| `meteorcli config --show` | Yes | Never prints secrets. |
| `meteorcli config meteorenv` | Python service did it at start | Applies a first-boot `meteorenv` file; `meteorcli.service` runs it as root before the agent. See [Enrollment](#enrollment). |
| `meteorcli status` | Yes, same lines | Asks the running agent over `agent.sock` (adds an `Agent:` line, the pending request id, and one line per module). When the agent is not running it reads the files and checks secrets by `stat` only. Never shows secrets. Exits 1 until enrolled. |
| `meteorcli register [--token T \| --token-file F] [--name N]` | Yes, without `--server` | The agent registers. `$METEORCLI_TOKEN` works too; `--token-file` is removed after success; the two flags cannot be combined. Uses the configured server. Needs `meteorcli-admin`. |
| `meteorcli request-token [--api-key K] [--name N] [--wait S] [--new]` (alias `request`) | Mostly | The agent submits the request and keeps polling in the background, also after a reboot. `--wait` also waits here. Exit codes: 0, 2 (not set up / already pending / already enrolled), 3 (key refused), 75 with a `retry-after: N` line (try later), 1. Needs `meteorcli-admin`. |
| `meteorcli claim [--wait S]` | Mostly | Reports the pending request (exit 0 enrolled, 2 still pending, 1 ended); the agent collects the approval by itself, so this is optional. Needs `meteorcli-admin`. |
| `meteorcli test` | Yes | The agent checks `/api/v1/health` and the saved API key. Only a 401/403 reports the key as rejected; other failures are errors. |
| `meteorcli module restart NAME` | New | Restarts (or unparks) one module of the running agent. Needs `meteorcli-admin`. |
| `meteorcli doctor [--json]` | New | The agent checks the server (`/api/v1/health`), enrollment, the last heartbeat, MQTT, the root helper (`ping`), free disk space and every module, and prints a hint for each problem. Exit 1 when a check failed. The console's **Run diagnostics** shows the same report. Needs `meteorcli`. |
| `meteorcli logs [SOURCE] [--json] [-n N] [--since S] [--grep T] [-f]` | Yes, same arguments | Without a source lists the sources (journal, systemd units, Docker containers, `/var/log` files); `SOURCE` is `journal`, `systemd:UNIT`, `docker:NAME` or `file:PATH`. Also takes the Python helper's `logs list` and `logs show KIND [NAME] [--project P] [--lines N] [--since S] [--grep T] [--follow]`, which the console runs. Members of `meteorcli` read through the agent and the helper; root reads in-process (works with the agent stopped). |
| `meteorcli mqtt-test [TOPIC] [MESSAGE]` | Yes, same arguments | The agent publishes through its own session (client id `device-<id>`), not retained; default a small JSON object on `status`. Fails with the last connection error while the agent is not connected. Needs `meteorcli-admin` (it speaks as the device). |
| `meteorcli mqtt-listen [TOPIC] [--timeout S]` | Yes, same arguments | Prints `<topic> <payload>` for each command the agent's session receives (only `commands` is subscribed). Needs `meteorcli`. |
| `meteorcli update` | Mostly | Asks the agent to start the updater and returns; follow it with `journalctl -u meteorcli-update -f`. Python's `--check`, `--to` and `--rollback` are not there yet. Needs `meteorcli-admin`. |
| `meteorcli updater [--action ID] [--health-timeout 3m]` | New | The updater itself (root); `meteorcli-update.service` runs it. Root may run it by hand to update at once and watch it. Exit 0 updated or already current, 1 otherwise. |
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

Today the socket serves `status`, `test`, `doctor`, `register`, `enroll.request`, `enroll.claim`,
`config.reload`, `module restart`, `mqtt.test`, `update.start` (admin), `mqtt.listen`, `logs.list`
and `logs.show` (read); the other
commands arrive with their modules. The agent's own
user (`meteor`) is always allowed, like root. `agent.sock` belongs to the `meteorcli` group, so an
administrator is in **both** groups:

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
a minute without requests (a followed log keeps it running). It serves at most 32 connections, all
from the agent, which follows at most 8 logs at once.

| Operation | Params | Does |
| --- | --- | --- |
| `ping` | none | Answers `{"ok": true}`; `meteorcli doctor` checks that the helper runs and accepts the agent |
| `power.reboot`, `power.shutdown` | none | `systemctl --no-block reboot` / `poweroff` (answers first) |
| `logs.read` | `source` (`journal` or `systemd`), `unit`, `lines` (1-10000, default 200), `since` (`15m`, `1h`, `7d`), `grep` (up to 200 printable characters) | `journalctl` with fixed options; returns `lines` |
| `systemd.install_unit` | `name` (`^[a-z0-9][a-z0-9._-]{0,62}$`, ending `.service`, never `meteorcli*`), `content` (needs `[Service]`, at most 256 KiB), `no_start` | Atomic write to `/etc/systemd/system`, `daemon-reload`, `enable`, `restart` |
| `users.set_password` | `username` (`^[a-z_][a-z0-9_-]{0,31}$`, not `root`, `meteor` or another system account), `password` (8-128 characters, no control characters) | `chpasswd` with the password on stdin; the user must exist |
| `users.add` | `username`, `password` (as above), `sudo`, `authorized_key` (one SSH public key line) | `useradd -m -s /bin/bash`, `chpasswd`, optional `/etc/sudoers.d/90-meteorcli-<user>` (0440, `NOPASSWD:ALL`) and `~/.ssh/authorized_keys`; a failure half way removes the user again |
| `docker.ps`, `docker.inspect`, `docker.stats` | `inspect`: `names` (1-20 container names) | The Python observer's fixed `docker ps -a`, `docker inspect -f <template>` and `docker stats --no-stream` commands; returns their output, which the agent parses |
| `logs.warnings` | `after_cursor` (optional journal cursor) | `journalctl --priority=warning --output=json`, at most 200 entries (the last 10 minutes without a cursor); returns message (cut to 1000 characters), priority, unit, source, time and cursor |
| `systemd.control` | `unit` (as `systemd.install_unit`, never `meteorcli*`), `action` (`is_active`, `restart`, `remove`) | `systemctl is-active --quiet` (answers `active`), `restart`, or `disable --now` plus deleting the unit file |
| `docker.load` | `path` (a file directly in `/var/lib/meteorcli/artifacts`, symlinks resolved and checked) | `docker load -i` (15 min) |
| `docker.run` | `name`, `image` (no leading `-`), `restart` (`no`, `always`, `unless-stopped`, `on-failure[:N]`), `ports` (`-p` values), `env` (names `^[A-Za-z_][A-Za-z0-9_.-]*$`, values without line breaks), `token_file` (a `token` under `/var/lib/meteorcli/app-credentials/<dir>/`), `command` (container arguments), `pull_never` | Prepares the environment in a 0600 `--env-file` in the helper's private `/tmp` (never on the command line), then `docker rm -f` and `docker run -d [--pull never]` (15 min, for the pull) |
| `docker.health`, `docker.remove` | `name` | `docker inspect -f '{{.State.Running}}\|{{.State.Status}}\|{{.State.ExitCode}}\|<health>'` (never env or labels) / `docker rm -f` |
| `logs.list` | none | The sources `meteorcli logs` offers: `journal`, loaded `systemd` service units, Docker containers (with their compose project) when the Docker socket exists, readable regular files from a fixed glob list (`/var/log/syslog`, `messages`, `kern.log`, `auth.log`, `daemon.log`, `dpkg.log`, `/var/log/nginx/*.log`, `/var/log/apache2/*.log`, ...) |
| `logs.show` (streamed) | `kind`, `name`, `project`, `lines` (1-10000), `since` (`15m`, `1h`, `7d`), `grep`, `follow` | Only a source `logs.list` returned: `journalctl [--unit]`, `docker logs --timestamps --tail`, `tail -n`/`tail -F`. Lines go out as they come (one frame per line, cut at 64 KiB); `follow` runs until the caller hangs up, at most 24 hours. `grep` scans the last 50 000 lines for the `lines` last matches, then filters followed lines |
| `wifi.status` | none | NetworkManager's state (`nmcli -t`, stdout only): `supported`/`unsupported_reason`, `connected_ssid`, `signal_percent`, `interface`, `wired_connected`, `saved_networks` (managed or not), `pending`, plus the uplink (`uplink_type`, `uplink_interface`) |
| `wifi.apply` | `id`, `revision`, `ssid` (1-32 bytes), `security` (`wpa-psk`, `open`), `password` (8-63 characters), `hidden`, `connect_now` | Writes a keyfile (0600, `O_EXCL`/`O_NOFOLLOW`) in `/etc/NetworkManager/system-connections` and `nmcli connection reload` (the password is never on a command line). Other profiles for the same SSID are backed up and removed. With `connect_now` it activates the profile (45 s) and keeps a pending record until `wifi.confirm`; a network it cannot join is rolled back at once (`not_joined`). Netplan definitions of that SSID are forgotten with `netplan set` when netplan exists |
| `wifi.confirm`, `wifi.rollback` | none | Drop the pending record and backups / put the backed-up profiles back, remove the new one and reconnect |
| `wifi.remove` | `ssid` | Deletes the profiles of that SSID; refused with `would_cut_off` when it is the device's only link |
| `wireguard.apply` | `address`, `private_key`, `public_key`, `endpoint` (`host:port`), `allowed_ips` (single private hosts), `keepalive` | Renders `wg0.conf` (no hooks, every value checked), refuses tunnel hosts that route to the local network (`ip route get`), writes it 0600 and runs `wg-quick up`; does nothing when wg0 already runs the same config |
| `wireguard.down`, `wireguard.show` | none | `wg-quick down` (or `ip link delete wg0`) / `wg show wg0 latest-handshakes` and `endpoints` |
| `wireguard.endpoint` | `public_key`, `endpoint` (IP literal and port) | `wg set wg0 peer K endpoint E` (the agent resolved the server's name again) |
| `update.start` | `action_id` (optional, letters, digits and `-`) | `systemd-run --unit=meteorcli-update --collect --no-block` with a sandbox (`ProtectSystem=strict`, writable only `/usr/local/bin` and `/var/lib/meteorcli`, `NoNewPrivileges`, `RuntimeMaxSec=30min`) running `/usr/local/bin/meteorcli updater [--action ID]`. One fixed unit name, so a second update is refused while one runs. Answers at once |
| `ssh.authorized_keys` | `gateway_key`, `gateway_key_retired`, `ca_key`, `principal` | Keeps the managed lines in the `ssh_user`'s `~/.ssh/authorized_keys` (the gateway key, `cert-authority,principals="…" <ca> meteorcloud-ca`); the user comes from `config.json`, never the request, and is never root or a system account. Answers the CA the user trusts. An empty request changes nothing |
| `compose.services`, `compose.up`, `compose.ps`, `compose.down` | `project` (`^[a-z0-9][a-z0-9_-]{0,62}$`), `file`, `env_file`, `override_file` (each directly in `/var/lib/meteorcli/compose/<project>/`), `force_recreate` | `docker compose -p <project> -f <file> [--env-file] config --services` / `[-f override] up -d --remove-orphans [--force-recreate]` (15 min) / `ps -a --format json` / `down --remove-orphans`; `services` and `ps` answer stdout only (warnings stay out of the parsed output) |

**Trust boundary.** `systemd.install_unit` checks the unit's name and shape, not what `ExecStart`
runs, and `docker.run` / `compose.up` start whatever image or compose file they are given, so
whoever controls the `meteor` user can run commands as root through them (as with Docker on a
device). The helper builds every docker command itself from typed fields (there is no field for
`--privileged` or a host mount in `docker.run`) and reads files only from the agent's artifact,
compose and credential directories, but a compose file can still ask for anything. The `meteor`
account is unprivileged, not confined: protect it like root. The agent applies only releases whose
signature it verified, except from an older control plane that does not sign (`/signing-keys`
answers 404 and no key is pinned yet; see [Deploy](#deploy)). The agent never gets the Docker socket and `meteor` is never in the `docker`
group.

Tools (`systemctl`, `journalctl`) are looked up only in a fixed root-owned `PATH`, never in the
helper's environment. `logs.read` streams `journalctl` output into a buffer of the requested
number of matching lines. `grep` searches every line in full (ignoring ASCII case); a line longer
than 16 KiB is returned cut, ending in `…[truncated N bytes]`.

Passwords and SSH keys are never in an audit line (`users.*` log the user name and `sudo` only)
and never on a command line. The helper unit hides `/root` and `/run/user` with
`TemporaryFileSystem=…:ro` and leaves `/home` alone, because `users.add` creates homes there.
(`ProtectHome=tmpfs` with `BindPaths=/home` remounts `/home` read-only on systemd 257;
`ProtectHome=read-only` with `ReadWritePaths=/home` fails the same way on systemd 252.)

Wi-Fi passwords and the WireGuard private key are never in an audit line (`wifi.*` log the SSID and
`connect_now`; `wireguard.apply` logs the address and endpoint). The helper unit allows `AF_INET`
and `AF_INET6` (wg-quick resolves a server given by name) and keeps its state in
`/var/lib/meteorcli-helper`.

### Packaging

[`src/device-plane/agent-go/packaging/`](../src/device-plane/agent-go/packaging/) holds
`meteorcli.service`, `meteorcli-helper.service`, `meteorcli-helper.socket`,
`sysusers.d/meteorcli.conf` (user `meteor`, groups `meteorcli` and `meteorcli-admin`) and
`tmpfiles.d/meteorcli.conf` (the directories above).

- `meteorcli.service`: `Type=notify`, `WatchdogSec=60`, `User=meteor`, no capabilities,
  `ProtectSystem=strict`, `SystemCallFilter=@system-service`, `MemoryMax=128M`, `Restart=always`.
  `systemd-analyze security` exposure 1.6.
- `meteorcli-helper.service`: root with a limited capability set, `NoNewPrivileges`, `PrivateTmp`,
  `ProtectKernelModules`, `TemporaryFileSystem=/root:ro /run/user:ro` and a system call filter.
  Exposure about 2.9 on systemd 257.
- The installer adds `/usr/local/libexec/meteorcli-logs` and its sudo rule (checked with
  `visudo -c`), installs `wireguard-tools` with apt unless `--no-wireguard`, sets `--ssh-user`
  from `--user`/`$SUDO_USER`. A `--user` that cannot be the SSH user (root, system
  accounts, names outside `^[a-z_][a-z0-9_-]{0,31}$`) only skips `--ssh-user`.
- `/run/meteorcli` comes from tmpfiles.d instead of `RuntimeDirectory=`: systemd would delete it,
  with `helper.sock`, whenever the agent stops, and would give it to `meteor:meteor`, locking the
  `meteorcli` group out. The agent has `SupplementaryGroups=meteorcli` so it can give `agent.sock` to
  that group without a capability.

CI fails when the agent's exposure reaches 2.0 or the helper's reaches 6.0.

## Enrollment

The CLI never talks to the server and never writes the agent's files: `register`, `request-token`
and `claim` are requests to the running agent, which does the HTTP calls and stores the result.
So a `meteorcli-admin` user enrolls a device without sudo, and the device is online within a few
seconds (the container test measures it).

1. **Server.** Root runs `meteorcli config --domain …` (or `--api-base`). If the agent cannot use
   the new settings (an unreadable pinned CA, say) it sends no requests at all, neither to the new
   nor to the old server, and retries until they are fixed. The agent picks the change
   up at once (`config.reload` over the socket, and an inotify watch on `/etc/meteorcli` and the
   identity directory, with a one-minute rescan when inotify is not available).
2. **Register** with a one-time token, or **request** approval with an enrollment API key. The key is
   saved in `identity/api-key`; the request id and claim secret in `identity/pending-claim.json`.
3. **Approval.** The agent polls the request in the background: no faster than every 10 s or the
   server's `poll_interval_seconds`, waiting as long as `Retry-After` asks on 429, and again right
   after it starts, so an approval given while the device was off is collected at boot. A request
   that expired is submitted again with the saved key; a rejected or unknown one is dropped and
   `status`/`claim` say why.
4. **Enrolled.** The identity is written through a staging directory with a `DONE` marker, then
   moved into place with `device.json` last (an interrupted write is finished or dropped at the next
   start; while it cannot be finished the agent keeps the identity it has in memory and retries).
   The agent stores an identity only under a `flock` on the identity directory, after checking that
   `config.json` still names the server that issued it (and, for an approval, that the request is
   still the pending one). `meteorcli config` holds the same lock while it drops one server's files
   and saves the next server, so a credential is never stored next to another server's URL. If the
   identity cannot be written, a collected approval is kept in memory and stored on a later try (the
   server hands the token out once). The pending claim, the API key and old signing keys are deleted. The `Enrolled` signal
   starts the other modules: modules other than `socket` and `identity` wait until the device is
   enrolled (they show as `waiting`).
5. **Credentials.** A heartbeat answered with 401/403 marks the token rejected: modules stop using it
   and the identity module checks it with `GET /api/v1/agent/mqtt`, from 30 s up to every 15 min,
   until the server takes it again (an operator re-enabling the device) or someone enrolls again.
   The token is not deleted. MQTT credentials are refreshed when the server's `config_version`
   changes (checked every heartbeat interval, at least every minute).

**First boot.** `meteorcli.service` runs `meteorcli config meteorenv` as root before the agent
(`ExecStartPre=-+`). It reads the first of `.meteorenv`, `meteorenv`, `meteorenv.txt`,
`.meteorenv.txt` in `$METEOR_BOOT_DIR`, `/boot/firmware` and `/boot`; takes `METEOR_DOMAIN`,
`METEOR_API_BASE`, `METEOR_API_KEY` and `METEOR_NAME`; saves them like `meteorcli config`; and
deletes the file. A device that is already enrolled ignores the file. When the agent starts with a
saved API key, a server, and neither an identity nor a pending request, it submits a request by
itself (retrying every minute while the network is down). If the server refuses the key, the agent
records a hash of it in `identity/auto-request.json` and does not send it again at later starts; a
new key, or an explicit `meteorcli request-token`, still works. Wi-Fi,
user and SSH settings in the same file stay with the image's first-boot scripts.

### Heartbeat and telemetry

The `heartbeat` module posts the inventory, the capabilities (installed tools, the modules' health)
and up to 100 queued software reports every `heartbeat_interval_seconds` (60 s by default), acks the
reports the server accepted, and tells the other modules about waiting work (`WorkWaiting`). It
uses the interval the server returns and stores it in `device.json`. A failed heartbeat is retried
after 1 s, doubling to 60 s, with jitter (each wait is 50–100% of the step). The inventory always has
all 13 contract keys, `null` when unknown.

`internal/platform` ports the Python platform adapters: board detection (Raspberry Pi, NVIDIA
Jetson, generic Linux), `/etc/os-release`, `/proc` and `/sys` readers, thermal zones, and tool
checks (`docker compose version` with a 10 s timeout). `internal/telemetry` builds the inventory and
metrics from it; the MQTT bus publishes the metrics in the next pull request. A tool probe is reused
for 10 minutes once Docker Compose is found; while it is missing, the probe runs again after 30 s,
and at once when the docker binary appears or goes away. Docker installed while the agent runs is
reported by the next heartbeats (the control plane skips a deployment whose runtime the device last
reported missing; create the deployment again once the capability shows up).

### MQTT

The `bus` package holds the device's one MQTT session (paho.golang, MQTT 5): client id
`device-<id>`, the username and password from `identity/mqtt.json`, TLS 1.2+ trusting only the
broker CA from enrollment (the system CAs when the server sent none; verification is never
skipped), keep-alive 60 s, a clean session, and the last will `{"status":"offline"}` (retained) on
`devices/<id>/status`. A broker address of `localhost` is replaced by the API's host, like the
Python agent. Modules publish through the router (QoS 1, only the contract's topics); while the
session is down a publish fails at once and nothing is queued, so a long outage cannot fill the
memory. The session reconnects by itself after 1 s, doubling to 30 s, with jitter; the heartbeat
does not depend on it.

- **`mqtt` module** (critical): runs the session from the identity's MQTT settings and starts over
  when they change (`CredentialsRotated`, `ConfigChanged`) or MQTT is turned off. After each connect
  it publishes `online` (retained, with `agent_version` and `uptime_seconds`) and again 2 s later.
  `ping` is answered on `commands/result`; `sync_desired` becomes a `WorkWaiting{software}` signal;
  anything else is ignored. A clean stop publishes `offline` and disconnects.
- **`metrics` module**: once connected and then every 60 s, the readings of `telemetry.Metrics`
  plus network rates, Wi-Fi signal, `agent_errors` (ERROR log lines since the last round) and the
  `app.<name>.<field>` keys of up to 20 Docker containers, then the events seen since the last
  round: app lifecycle (`app.started`, `app.exited`, `app.restarted`, `app.oom_killed`,
  `app.healthy`, `app.unhealthy`, `app.crash_loop`, `app.removed`) and journal warnings (`log`).
  Docker and the journal are read through the helper; the first look at the containers is the
  baseline. While disconnected the latest metrics body and up to 500 events wait in memory for
  the next round. `METEORCLI_METRICS_INTERVAL` (seconds) changes the interval for tests and development.
- `meteorcli status` shows the connection (`MQTT broker: connected to host:8883`, or the last error).

### Actions

When the heartbeat answer says `actions_pending`, the `actions` module collects the actions
(`GET /actions`, the only time a password travels) and runs them in order through the helper.
`reboot` and `shutdown` are reported `done` before they run. `update_agent` is reported once the
update has settled (see [Updating](#updating)). `restart_module` asks the supervisor (signal
`RestartModule`) to restart that module, so a parked one runs again; an unknown module is reported
failed. `run_diagnostics` reports `done` with the `meteorcli doctor` report as `output` (redacted,
at most 16000 characters). See [Device actions](fleet/device-actions.md#module-health-and-remote-repair). The other results are reported together
afterwards; an unknown kind is reported `failed` with an error that starts with `Unsupported`. Every result waits in the outbox (`state/outbox`, kind
`action_report`) until the server accepted it, so a result survives a network outage and the
reboot itself; reports are retried with backoff, at most 50 per request.

### Updating

`update_agent` from the console, or `meteorcli update` by a `meteorcli-admin`, goes through the
root helper (`update.start`), which starts `meteorcli updater` as the transient unit
`meteorcli-update.service`. It runs outside the agent's and the helper's units, so restarting them
cannot stop it. For an action the agent first writes `state/agent-update.json`.

1. **Release.** The updater reads `meteorcli-release.json` and `meteorcli-release.json.sig` from
   `release_url` (default: `releases/latest/download`, the newest `agent-v*` release). The manifest has the version and the
   SHA-256 and size of each binary; the signature is ed25519 over its exact bytes, checked against
   the release public key compiled into the binary (`internal/update/key.go`). An unsigned manifest,
   or one signed by any other key, is refused before anything is downloaded. A redirect is followed
   (GitHub release files redirect to their storage) but never from `https` to `http` on any hop and
   never to a URL with credentials; no credential is sent. `release_url` is a folder URL without a
   user name, password, query or fragment. `http://` and `file://` URLs are allowed because the signature, not the transport,
   is what the updater trusts.
2. **Version.** A release at the installed version, or older, ends with `already_current` (like the
   Python updater). Versions compare as semver; build metadata (`+commit`) is ignored; edge builds
   are `0.0.0-edge.<run number>+<commit>`.
3. **Stage.** The binary for this CPU (`meteorcli-linux-arm64`, `-armv6`, `-amd64`) is downloaded to
   `/usr/local/bin/.meteorcli.new` (the same filesystem, and `/tmp` is often `noexec`), checked
   against the signed size and SHA-256, and run once with `--version`, which must print the
   manifest's version. It needs the binary's size plus 16 MB free.
4. **Swap.** The installed binary is kept as `/usr/local/bin/meteorcli.previous` (a hard link) and
   the new one is renamed over `/usr/local/bin/meteorcli`, so the path is never missing.
5. **Start.** `systemctl restart meteorcli.service` (it waits for the agent's `READY=1`), then the
   running helper is stopped so its socket starts the new binary at the next request.
6. **Health.** Within `--health-timeout` (3 minutes) the agent must report the new version on
   `agent.sock` and, when enrolled, a heartbeat the server accepted since it started
   (`last_heartbeat` in `status`); a refused credential (`credential_rejected`) is never healthy.
   Not enrolled, it must keep answering for 10 s without a break. The check is tied to the
   activation the restart created: systemd resets `NRestarts` on a manual restart, so any
   automatic restart, or another `InvocationID`, means the new version crashed. That, a unit that
   is no longer active, a failed start or the timeout count as unhealthy.
7. **Rollback.** Then `meteorcli.previous` is put back (it stays as `.previous` too), the agent is
   restarted on it and the helper stopped.

The result goes to `state/agent-update-outcome.json`: `updated`, `already_current`, `failed`
(nothing changed) or `rolled_back`, with the error. The `actions` module checks it every 10 s while
an update runs, reports the action `done` (updated, already current) or `failed` with the
updater's error (for example `meteorcli 0.3.0 did not start: …; rolled back to 0.2.0`), and removes
both files once the result is in the outbox. Without an outcome after 30 minutes the action is
reported failed. A lock (`/run/meteorcli-update/lock`) keeps a second updater from running.
While an `update_agent` action waits for its outcome, a second `update_agent` is reported failed
and `meteorcli update` (or `sudo meteorcli updater`) is refused, so the one outcome file is never
replaced before the action is reported.

Only the binary is replaced: unit files, sysusers.d and tmpfiles.d stay as the installer wrote
them. A release that changes them needs the installer (`install-meteorcli.sh`) once.

**Release signing.** `scripts/dist.sh` signs the manifest with `$METEORCLI_RELEASE_SIGNING_KEY`
(`go run ./tools/release sign <dir> <version>`); the release jobs take it from the repository
secret of that name. The edge job uploads the binaries before the manifest; without the secret its
release is unsigned and devices refuse it. The `agent-v*` job fails instead of publishing an
unsigned release. A new key pair comes from `go run ./tools/release genkey`: the
private key goes into the secret, the public key into `internal/update/key.go` (several keys,
comma separated, allow a rotation: ship a build that trusts both before signing with the new one).
Test builds trust another key with `METEORCLI_RELEASE_KEYS=<public key> scripts/build.sh` (or
`-ldflags -X meteorcloud/meteorcli/internal/update.releaseKeys=<key>`).

Changed from the Python updater: one static binary is swapped instead of a virtualenv tree; the
release is verified with an ed25519 signature as well as a checksum; health means a heartbeat from
the new version, not only an active unit; `--check`, `--to`, `--allow-downgrade` and `--rollback`
are not there yet.

### Deploy

The `deploy` module (`internal/deploy`) installs the desired software. It runs after every
heartbeat (the heartbeat publishes `WorkWaiting{software}`; so does an MQTT `sync_desired`) in its
own goroutine, so a slow image pull never delays a heartbeat. One cycle is the Python agent's
`reconcile_software`, step for step:

1. `GET /software`. An unknown kind is reported `failed: Unsupported release kind: '<kind>'`.
2. Each item is checked with `internal/signing`: Ed25519 over `meteorcloud-desired-v1\n` plus the
   canonical JSON of the signed fields (byte for byte Python's `json.dumps(sort_keys=True,
   separators=(",", ":"), ensure_ascii=False)`), with the organization keys from `GET /signing-keys`
   pinned on first use in `state/signing-keys.json` (tied to the device token); a new key is pinned
   only when a pinned key endorsed it. A `404` from `/signing-keys` (an older server) accepts
   unsigned items until keys exist. A revision older than the applied one is refused.
3. A workload already at the desired release and revision only reports its health (`in_sync` plus
   `healthy`, `unhealthy` or `unknown`); one that is gone (`docker rm`) or whose token file was lost
   is installed again.
4. Otherwise the artifact is downloaded (`GET /artifacts/{id}/download` with
   `X-Meteorcli-Redirect: 1`; a redirect to storage is followed without the token and without a
   `User-Agent`, never `http` after `https`, only with `X-Checksum-SHA256`) into
   `artifacts/<id>`, streamed with its SHA-256 checked against the server's and the signed
   `artifact_sha256`, and reused while `<id>.sha256` matches. A download may take 30 minutes but is
   cut after 5 minutes without data. Progress goes out as `updating` reports (0, every 10 %, 100)
   in heartbeats that carry only `software_report`.
5. The runtime applies it through the helper (`systemd.install_unit`, `docker.load` and
   `docker.run`, `compose.up`) and checks it right after; a container or unit that is not up fails
   the install. An image from an artifact runs with `--pull never`, so a tag missing from the
   tarball fails instead of being pulled. A compose release replaces the compose file of the one
   before it (other files in the project directory stay); `spec.env` goes in `.env.meteorcloud`
   with every value quoted, so Compose takes it literally (no `${VAR}` interpolation). A compose
   service without any container, or a container docker says does not exist, is installed again;
   a Docker daemon that does not answer is reported `unhealthy`, nothing is replaced.
6. With a `health_check` in the spec, the workload must be healthy (runtime, plus `http_url`
   answering 2xx/3xx without following redirects) within `timeout_seconds` (60) and stay so for
   `stable_seconds` (10). If not, and `rollback` is not `false`, the previous release recorded in
   `state/applied-software.json` is applied again and reported `rolled_back`; the failed release is
   not tried again until the desired release changes.

Bound workloads (`spec.meteorcloud.application`) get a token from `POST
/applications/credentials`, kept in `app-credentials/` and mounted read-only at
`/run/secrets/meteorcloud-token`, with the `METEORCLOUD_*` variables; it is rewritten in place
(same inode) when due, so the container sees the new token without a restart. Rotation is the
`appcreds` module's job (every minute it rewrites the tokens past `refresh_after`); deploy mints a
token when a workload is installed. A rotation re-checks the stored binding under the store's lock,
never creates a missing token file (deploy then recreates the container, whose mount needs the new
inode), and keeps unknown `meta.json` keys; a damaged `meta.json` is moved to
`meta.json.corrupt-<time>`. A release the device rolled back to keeps its token fresh too. The server issues credentials for the desired release's
binding, so a credential for another application or environment is refused: a rollback to a
release bound to a different application fails instead of running with the wrong token. Stored
credentials are deleted only when verified desired software no longer binds the workload.

Each report is kept in the outbox (kind `software_report`, one per workload, latest wins). The
outcome of an install attempt is also sent at once in a heartbeat with only `software_report` and
removed from the outbox when the server accepted it; health-only reports go with the next regular
heartbeat. Install outcomes are also announced on `devices/{id}/events` as `deploy.in_sync`,
`deploy.failed` or `deploy.rolled_back` (with `workload_key`, `release_version`, `health`,
`error`), once per change. A runtime is a package under `internal/deploy/runtime/` plus one line in
`internal/deploy/runtimes.go`.

### Wi-Fi

The `wifi` module waits for a heartbeat that says Wi-Fi changes wait (`wifi_pending`), collects
them with `GET /network/wifi` (the only moment a password travels) and hands each to the helper.
A `connect_now` change is kept only when `GET /api/v1/health` answers on the new network within
90 seconds (tried every 5 seconds); otherwise the old settings come back and the result is
`rolled_back` with the console's message. A network that cannot be joined is `rolled_back` at
once. A change a restart or power cut left unconfirmed is rolled back when the agent starts and
reported `rolled_back` ("The device restarted before the new network was confirmed…").
A `connect_now` change turns the Wi-Fi radio on first when it is switched off (Raspberry Pi OS
boots with it soft-blocked until a Wi-Fi country is set), waits for the adapter and scans; a radio
that stays off (hardware block) fails with `radio_off` before anything changes. A failed join says
whether the password was rejected or the network was not found. Save-for-later changes leave the
radio as it is. Confirm and rollback are tried three times; a change that cannot be confirmed is rolled back,
and one whose rollback fails is reported `failed` and rolled back by the next status check
(every minute), which then reports it `rolled_back`. An action other than `apply` or `remove`
is reported `failed` without touching the device. Helper changes are serialized; a restore that
cannot put every old profile back keeps the pending record and backups for the next attempt.

Results wait in the outbox (kind `wifi_result`) until `POST /network/wifi/report` accepted them.
The state goes with them when it changed, at least every 5 minutes (checked every minute, first 15
seconds after start) and after every collection. Without NetworkManager the report says
`supported: false` with the reason, and the uplink the agent reads from `/proc/net/route`.

### WireGuard

The `wireguard` module makes an X25519 key pair in `identity/wireguard/` (the Python agent's
private key is used as is; the public key is always derived from it, and a missing or wrong
`public.key` is rewritten; the private key never leaves the device) and registers it with
`POST /connectivity/wireguard`, together with `ssh_user`, the sshd host key (pinned by the
server) and the SSH CA the user already trusts. It registers again every 15 minutes (×0.8-1.2),
at once after enrollment, a credential rotation or a config change, and with backoff (30 s to
30 min) after failures. `connectivity_disabled` stops it for 30 minutes;
`connectivity_revoked` and `wireguard_key_changed` take wg0 down (retried until it is gone) and
wait for an operator; the refusal is kept in `state/wireguard.json`, so the saved settings are not
used again, also after a restart during an outage, until a registration succeeds. The same
refusals on the immediate re-registration that reports a new SSH CA are handled the same way.

wg0 is brought up through the helper and kept up every minute from the last settings in
`state/wireguard.json`, also while the server is unreachable. Only settings the helper applied are
saved, and the module reports `running` only once wg0 runs them. The helper checks the routes to
the tunnel hosts before it touches a working wg0 (a route it cannot check is refused) and brings
the previous config back when a new one fails to start. When the server is given by name
and no handshake happened for 3 minutes, the agent resolves the name again (IPv4 first, IPv6 when the name
has only AAAA records) and points the peer at the new address. The SSH lines from the answer go to the SSH user's `authorized_keys`; when the
user trusts a new CA the agent registers again at once so the console can use certificates.
Without `wg` and `wg-quick` the module waits and looks again every 10 minutes.

### Differences from the Python agent

- The agent, not the CLI, does enrollment; the CLI needs `meteorcli-admin`, not root.
- `register` has no `--server`: set the server with `meteorcli config` first.
- `request-token` on an enrolled device needs `--new`; with a request pending it reports that
  request (or its approval) instead of submitting another.
- Approval is collected by the agent in the background, also across reboots; `claim` is optional.
- The API key is deleted once the device is enrolled (Python kept it). Enrolling again needs a key.
- The automatic request (meteorenv) runs when the agent starts and is not repeated after the
  server refuses the key, also across restarts (Python retried at every start until enrolled).
- A 401/403 pauses the token and re-checks it with backoff; Python exited the service.
- The heartbeat follows the server's `heartbeat_interval_seconds` (Python kept its own interval).
- The agent runs unprivileged, so a root-only DMI serial (`product_serial`) may be missing from
  the inventory; the CPU or device tree serial, or a readable `board_serial`, is used instead.
- `capabilities.actions` lists `reboot`, `shutdown`, `set_password`, `add_user`, `update_agent`
  and the new `restart_module` and `run_diagnostics` (Python reported those two `failed:
  Unsupported...`). `deploy_kinds` lists `systemd`, `docker`, `docker_compose`.
- Deploys run through the root helper: the agent never has the Docker socket and is not in the
  `docker` group. `docker run` gets the release's environment from a private `--env-file`, so an
  env value may not contain a line break (Python passed `-e KEY=VALUE`, visible in `ps`). Unit,
  container and project names follow the helper's rules (units `^[a-z0-9][a-z0-9._-]{0,62}$`
  ending `.service`; Python's helper also took capitals, `@` and `:`). Private registries need
  credentials in `/etc/meteorcli/docker` (Python used the agent user's `~/.docker`).
- Failure texts from Docker read `docker run <name> failed: <docker output>` (Python printed the
  whole command line, which held the environment).
- Install outcomes are sent at once and kept in the outbox until accepted; health-only reports go
  with the next heartbeat instead of the same one. Deploy results are also published as
  `deploy.*` events (new, additive).
- A compose bundle may not contain entries outside the bundle (Python silently stripped `..`), is
  limited to 2000 files and 1 GiB unpacked, and its symlinks are not created. Bundle directories
  are 0755 and files 0644 whatever the agent's umask.
- A download that stalls for 5 minutes is cut (Python: a 300 s socket timeout per read; no total
  limit). A storage error after the redirect (an expired link) fails the install; it no longer
  counts as a refused device token.
- Compose `spec.env` values are quoted in `.env.meteorcloud` (Python wrote them bare, so Compose
  interpolated `$VAR` and cut `#` comments). An image from an artifact is never pulled
  (`--pull never`). A compose service whose container was removed is brought back; a docker
  inspect error is `unhealthy`, not a reason to recreate the container (Python treated any inspect
  failure as a missing container). A desired software answer that is not a JSON array is an error,
  not "nothing desired".
- MQTT 5 instead of 3.1.1 (same topics, QoS, retain, will and client id; Mosquitto and EMQX speak
  both). `METEORCLI_MQTT_TLS_INSECURE` is gone: TLS is always verified.
- `mqtt-test` and `mqtt-listen` use the agent's session over `agent.sock` instead of a second
  connection with the broker password, so they need the agent running (and no sudo).
- Action results are kept in the outbox until the server accepts them. If the `done` of a reboot
  cannot be sent, the device still reboots and sends it afterwards (Python skipped the reboot and
  lost the action). The second publish of `online` is skipped when it cannot be sent, as before.
- Wi-Fi and WireGuard go through the root helper instead of `sudo meteorcli-wifi` /
  `meteorcli-wg`. Wi-Fi results wait in the outbox until accepted (Python kept them in memory).
  The Wi-Fi state is reported every 5 minutes or on change, not in every heartbeat cycle.
- `ssh_user` comes from `config.json` (`meteorcli config --ssh-user`), not from the account that
  ran the installer at run time. Without it the device registers without `ssh_user` and the
  helper leaves `authorized_keys` alone.
- `ssh_host_key` is sent as `<type> <base64>`, without the file's comment (as Python did; the
  contract recordings now match that).
- `meteorcli logs` reads through the agent for group members (Python's CLI ran the helper with
  sudo). The console's `meteorcli-logs` path keeps working as a wrapper. `--lines 0` means the
  default (200).
- Application tokens are rotated by the `appcreds` module instead of the deploy cycle.
- While disconnected only the latest unsent metrics body is kept and sent on reconnect (Python's
  client queued every message and sent a burst). Events (app changes, journal warnings) wait in
  memory, at most 500, and go out after the metrics once connected.

## How the core works

- **Modules** implement `Name()`, `Critical()` and `Run(ctx, Deps)` and are listed in
  `internal/modules/modules.go` (`identity` and `socket` are core parts the agent adds itself).
  They never import each other. They cooperate through the signals hub
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
packages, the `systemd-analyze security` gate, the systemd and installer container tests, fails when a
binary is over 15 MB, and uploads the release files as a run artifact.

```bash
make agent-go-systemd-test   # Docker, --privileged: Debian 12 with systemd as PID 1
make agent-go-install-test   # the same container: install-rpi.sh, install-meteorcli.sh and first boot end to end
```

The install test refuses a board that is not a Pi. It then:
- installs with `--force` and `--token` (the device online right away, `pi` running `status` without sudo)
- runs the installer again as an upgrade (identity kept)
- installs on a fresh device with `--token` and checks it is online right away
- checks that no secret reaches the journal

The container test installs the units, sysusers and tmpfiles files, creates test users, and checks:
a `meteorcli-admin` user runs `status` and `module restart` without sudo, a `meteorcli` user may run
`status` but not `module restart`, a user in neither group is refused, the helper refuses every uid
except `meteor` (also root, and a user in the `meteor` group), `logs.read` and
`systemd.install_unit` work for `meteor`, the audit lines are in the journal, and no token is
logged; `users.add` creates a user with a home directory, a sudo rule and an SSH key,
`users.set_password` changes the hash in `/etc/shadow`, root's password is refused, `logs.warnings`
answers, and no user password reaches the journal. Then it enrolls against a small fake server (`test/systemd/fakeserver`): root changes the
server (the old identity is dropped), `alice` (`meteorcli-admin`) runs `register` without sudo and
the first heartbeat must arrive within 5 s; `alice` runs `request-token`, the request is approved
while the agent is stopped, the container restarts, and the device must come online with no
command; a `meteorenv.txt` on `/boot/firmware` is applied at start, removed, and the agent asks for
approval by itself. Today's binaries are about 7.1-7.5 MB (arm64, armv6, amd64; stripped).

The API client tests replay the recorded exchanges in
[`tests/agent-contract/recordings/`](../tests/agent-contract/recordings/) (first recorded from the
Python agent): same routes, headers, body keys, and the answers decode. The contract suite
(`make agent-contract-test`, CI job *Agent contract*) drives the Go agent through every scenario;
its transcripts must equal the recordings.

One third-party Go module: `github.com/eclipse/paho.golang` v0.22.0 (EPL-2.0 or EDL-1.0, used
under EDL-1.0; the same version the data plane uses). Only its `paho` and `packets` packages are
linked; `autopaho` is not used because it would pull in `gorilla/websocket` and `golang.org/x/net`.
staticcheck (MIT) and govulncheck (BSD-3-Clause) run in CI and are not shipped; neither is the
test-only `debian:12.8-slim` image ([licensing](licensing.md)).
