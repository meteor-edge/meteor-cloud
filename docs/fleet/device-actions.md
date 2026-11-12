# Device actions

Restart, shut down, change an SSH password, add an SSH user, or update meteorcli
from the console without logging into the device.

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

An action the device never collects is cancelled after
`DEVICE_ACTION_TTL_SECONDS` (default 1 hour).

The Settings panel lists recent actions and polls every 5 seconds while one is
waiting or in progress (otherwise every 30 seconds). Use the refresh control to
reload immediately; the device overview status is refreshed too. No websocket —
actions only move on heartbeats.

## Permissions

| Action | Permission | Default roles |
|--------|------------|---------------|
| Restart, shut down | `device.reboot` | owner, admin |
| Update meteorcli to the latest release | `device.update_agent` | owner, admin |
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
| `GET` | `/api/v1/agent/actions` | device credential; passwords included once |
| `POST` | `/api/v1/agent/actions/report` | body: `{results:[{id, state, error?}]}` |
