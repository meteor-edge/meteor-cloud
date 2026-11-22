# Device actions

Restart, shut down, change an SSH password, add an SSH user, update meteorcli,
restart one of its modules or run its diagnostics from the console without logging
into the device.

## How it works

1. An owner or admin orders the action on the device's Settings tab.
2. The next heartbeat tells the device something is waiting (`actions_pending`).
3. The device collects the action once with its own credential. A password is
   erased from the server at that moment. The same action is never handed out
   again, so a restart ordered while the device was offline does not run hours
   later after an unrelated reboot.
4. On the device `meteorcli.service` (`meteorcli run`) collects the action after
   the heartbeat. The Linux platform adapter calls the root-owned helper
   `/usr/local/libexec/meteorcli-action` (installed by `sudo ./installcli.sh`).
   Other boards/OS images can swap that adapter; the action kinds stay the same.
5. The device reports what happened. Restart and shut-down are reported before
   the system goes down. An agent update starts `update-meteorcli.sh` as its own
   process and reports done or failed on a later heartbeat once the version that
   is running has changed (or the updater finished without changing it).
   The Go meteorcli starts `meteorcli updater` through its root helper instead:
   it installs only a release whose manifest carries the MeteorCloud release
   signature, keeps the previous binary, and puts it back when the new version
   sends no heartbeat within 3 minutes; the action is then reported failed with
   the reason. See [meteorcli in Go: Updating](../meteorcli-go.md#updating).

An action the device never collects is cancelled after
`DEVICE_ACTION_TTL_SECONDS` (default 1 hour).

The Settings panel lists recent actions and polls every 5 seconds while one is
waiting or in progress (otherwise every 30 seconds). Use the refresh control to
reload immediately; the device overview status is refreshed too. No websocket —
actions only move on heartbeats.

## Only what the agent supports

Each heartbeat carries `capabilities.actions` and `capabilities.deploy_kinds`: what the
agent's code can do. The console offers only those actions, and the API refuses an
action the device's agent did not list (`409 action_unsupported`). A deployment skips
a device whose agent does not list the release's kind ("The device's agent cannot
install … releases."), as it already skipped a device without the runtime. An agent
that sends no list (older releases) keeps every action and kind.

## Module health and remote repair

The Go meteorcli runs each feature (heartbeat, MQTT, deploy, Wi-Fi, …) as its own
module. A module that crashes is restarted with backoff; one that fails five times in
ten minutes is stopped (*parked*) for an hour while the rest keep working. Every
heartbeat sends the health of each module (`capabilities.modules`); the server keeps
the latest report on the device (`metadata.capabilities`, with
`metadata.capabilities_reported_at`).

The device's **Overview** tab shows an **Agent modules** panel: each module with its
state (*Failed (stopped)* for a parked module, *Failed (restarting)* while it is being
retried), restart count and last error, broken modules first. Owners and admins can:

- **Restart** a module (`restart_module`): the agent restarts it, and a parked module
  runs again. Fix the cause first, or it will be parked again after five failures.
- **Run diagnostics** (`run_diagnostics`): the agent runs `meteorcli doctor` (server,
  enrollment, heartbeat, MQTT, root helper, disk space, every module) and sends the
  report, shown under the panel. The report never contains a secret.

Both go through the same queue as the other actions. The new module state shows with
the next heartbeat. An agent without module health (the Python agent) shows a note
instead of the list.

## Permissions

| Action | Permission | Default roles |
|--------|------------|---------------|
| Restart, shut down | `device.reboot` | owner, admin |
| Update meteorcli to the latest release | `device.update_agent` | owner, admin |
| Restart an agent module, run diagnostics | `device.update_agent` | owner, admin |
| Change password, add SSH user | `device.users` | owner, admin |

Passwords never appear in API responses, logs or the audit trail. The audit log
records who ordered which action on which device (and which username for user
actions).

## On the device

`meteorcli-action` accepts:

- `reboot` / `shutdown` — starts `systemctl reboot` or `poweroff`
- `set-password` — JSON `{username, password}` on stdin; runs `chpasswd`
- `add-user` — JSON `{username, password, sudo?, authorized_key?}`; creates a
  login user with a home directory, optional passwordless sudo, optional
  authorized key

Reserved accounts (`root`, `daemon`, `sshd`, …) cannot be changed from the
console. The username must match Debian's default `useradd` rules.

Needs meteorcli **0.3.3** or newer (`ActionSync` in `meteorcli run` plus the
`meteorcli-action` helper).

## API

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/devices/{id}/actions` | recent actions; never a password |
| `POST` | `…/actions/reboot` | |
| `POST` | `…/actions/shutdown` | |
| `POST` | `…/actions/set-password` | body: `{username, password}` |
| `POST` | `…/actions/add-user` | body: `{username, password, sudo?, authorized_key?}` |
| `POST` | `…/actions/update-agent` | |
| `POST` | `…/actions/restart-module` | body: `{module}`; `422 module_unknown` when the agent reported modules without it |
| `POST` | `…/actions/run-diagnostics` | the report comes back as the action's `output` |
| `GET` | `/api/v1/agent/actions` | device credential; passwords included once; `module` for `restart_module` |
| `POST` | `/api/v1/agent/actions/report` | body: `{results:[{id, state, error?, output?}]}` (`output`: diagnostics, at most 16000 characters) |
