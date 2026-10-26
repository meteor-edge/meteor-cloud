# Device logs

The device page's **Logs** window lists the logs a device has and lets you
follow and search them: services deployed with MeteorCloud (systemd, Docker,
Docker Compose), other systemd units and containers, nginx and system log files
under `/var/log`, and the whole systemd journal. Nothing is stored in
MeteorCloud; lines are read from the device when you open a source.

```text
console ── GET log-sources ──▶ ssh -T over WireGuard ──▶ sudo -n meteorcli-logs list
console ── POST log-sessions ──▶ ticket ──▶ websocket ──▶ ssh -T over WireGuard
        ──▶ sudo -n meteorcli-logs show … ──▶ journalctl / docker logs / tail
```

Logs use the same private path as the [Terminal](connectivity.md): SSH over the
device's WireGuard tunnel, with the pinned host key and a short-lived
certificate. They do not use MQTT: the broker is optional, and log volume does
not belong in the telemetry ingest path.

## Requirements

- WireGuard connectivity and `sshd` on the device, as for the Terminal.
- The `meteorcli-logs` helper. `sudo ./installcli.sh` (or the release installer)
  installs it root-owned at `/usr/local/libexec/meteorcli-logs`, plus
  `/etc/sudoers.d/meteorcli-logs`: passwordless sudo for that one file. The
  device user does not need to be in the `adm`, `systemd-journal` or `docker`
  groups (`docker` is root-equivalent). Re-run the installer after upgrading
  from an agent without it.
- The `device.logs` permission. Owner, admin, operator and developer roles have
  it; viewers do not. It is separate from `device.shell` (owner/admin only), so
  people can read logs without getting a shell.

## Sources

When the Logs window opens, the control plane runs `meteorcli-logs list` on the
device over the same SSH path, so the list is always current. The console
groups the sources:

| Group | What |
| --- | --- |
| Deployed by MeteorCloud | systemd units, containers and Compose project containers from [Deployments](deployments.md) |
| Containers | Other Docker containers (`docker ps --all`) |
| Services | Other loaded systemd services |
| Log files | `/var/log/nginx/*.log`, `/var/log/syslog`, `/var/log/messages`, `/var/log/kern.log` (regular files only, no symlinks) |
| System | The whole journal |

## Viewing and searching

- **Lines / Since**: the last N lines (up to 5000), optionally only from the
  last 15 minutes to 7 days. `Since` does not apply to log files.
- **Follow**: keep streaming new lines (`journalctl -f`, `docker logs -f`,
  `tail -F`). The view is a read-only terminal (like the Terminal window), so
  colors are kept and it keeps the last 20,000 lines.
- **Search**: a case-insensitive text search done by the helper on the device.
  It finds the last N matching lines among the most recent 50,000, then (with
  Follow) streams new matching lines.
- **Download** saves the lines shown.

A log session counts toward `SHELL_MAX_SESSIONS_PER_USER` and ends at
`SHELL_MAX_DURATION_SECONDS`. Unlike the Terminal it has no idle timeout,
because a followed log can stay quiet. Revoking access, or disabling the device,
ends open sessions at the next recheck. Opening and closing a session is
audited (`device.logs.start`, `device.logs.end`, with the source).

## Security

- The control plane builds the helper's argv from validated fields (kind, name,
  line count, since, search text) and quotes every argument. The browser never
  sends a command.
- With SSH certificates, a log session's certificate carries
  `force-command=<that command>` and no `permit-pty`, so it cannot open a shell.
- On the device the helper checks everything again before it runs anything:
  the source must be in its own current listing, names cannot start with `-`,
  files must match the fixed globs, and it runs fixed argv without a shell.
  When the SSH session closes, a followed log stops.

## On the device

```bash
meteorcli logs                       # list sources
meteorcli logs --json
meteorcli logs journal -n 100
meteorcli logs systemd:nginx.service -f --grep error
meteorcli logs docker:web --since 1h
meteorcli logs file:/var/log/nginx/access.log -f
```

## API

| Method | Path | Permission |
| --- | --- | --- |
| `GET` | `/api/v1/organizations/{org}/devices/{device}/log-sources` | `device.logs` |
| `POST` | `/api/v1/organizations/{org}/devices/{device}/log-sessions` | `device.logs` |
| WebSocket | `/api/v1/log-sessions/{session}/ws` | one-time ticket |

`POST log-sessions` takes `{"kind": "systemd", "name": "nginx.service",
"lines": 500, "since": "1h", "grep": "error", "follow": true}` (`kind` is
`journal`, `systemd`, `docker` or `file`; `journal` has no `name`) and returns
a ticket like a shell session. Send `{"type": "auth", "ticket": "…"}` as the
first websocket frame. Binary frames are log output; text frames are status
lines (`error: …`, `[end of log]`).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `log_sources_unavailable` ("did not list its logs") | Re-run `sudo ./installcli.sh`, check `meteorcli logs` on the device |
| `sudo: a password is required` in the log view | The SSH user is not the user `installcli.sh` set up the sudo rule for; re-run it with `sudo` as that user |
| A container or unit is missing | Click **Refresh** on the source list; it is listed as soon as it exists |
| `source not found` in the log view | The source was removed or renamed on the device; refresh the list and pick it again |
