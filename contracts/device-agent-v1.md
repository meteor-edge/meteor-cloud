# Device agent contract v1

How `meteorcli` on a device talks to MeteorCloud: HTTP to the control plane
(`/api/v1/agent/*`) and MQTT to the broker (`devices/{device_id}/...`). Any agent,
Python or Go, must speak exactly this.

- **Schemas:** [`schemas/agent/`](schemas/agent/) (JSON Schema 2020-12). The map of
  route or topic to schema is [`schemas/agent/index.json`](schemas/agent/index.json).
- **Examples:** [`tests/agent-contract/recordings/`](../tests/agent-contract/recordings/)
  hold real exchanges of the Python agent, with ids and secrets replaced by placeholders.
- **Checked by:** the control-plane tests (every `/api/v1/agent/*` answer in
  `src/control-plane/tests` is validated against the schemas) and the agent contract
  suite ([`tests/agent-contract/`](../tests/agent-contract/README.md)).

## Rules that apply everywhere

| Rule | Detail |
| --- | --- |
| Protocol version | Every agent request sends `X-Meteor-Agent-Protocol: 1` and `User-Agent: meteorcli/<X.Y.Z>`. A request without them is treated as protocol 1 (older agents). |
| Additive changes | New fields, routes, action kinds and deploy kinds keep protocol 1. Removing or renaming a field, or changing its meaning, needs protocol 2. |
| Unknown fields | Both sides ignore fields they do not know. Agents keep unknown keys when they rewrite a stored file. |
| Unknown kinds | An action or deploy kind the agent does not know is reported `failed` with an error that starts with `Unsupported` (case-insensitive), never silently dropped. |
| Errors | Every non-2xx JSON answer is `{"error": {"code": "...", "message": "..."}}`. Agents branch on `code`, never on `message`. |
| Auth | Device routes take `Authorization: Bearer <device token>`. Enrollment-key routes take `Authorization: Bearer <API key>`. Credentials travel only in headers, never in a URL. |
| 401 / 403 | On a device route: the credential is invalid or revoked. The agent stops using it and waits to be enrolled again; it does not hammer the server. |
| Retries | A failed heartbeat (network error, 429, 5xx) is retried with exponential backoff and jitter: 1 s doubling to 60 s, each delay multiplied by a random factor 0.5 to 1.0. Other calls are tried again on the next cycle. Agents should honour `Retry-After` (seconds) on 429/503; the Python agent does so for enrollment polling only. |
| Transport | HTTPS with TLS 1.2+ in production, system CAs plus an optional pinned CA. MQTT always uses TLS. |
| Secrets | Device token, MQTT password, claim secret, API key, Wi-Fi and user passwords are never logged. |

## Getting an identity

### Registration token

`POST /api/v1/agent/register` with `{"token": "...", ...inventory}` and no
Authorization header. Answer `201`: `device_id`, `device_token`, `organization_id`,
`name`, `heartbeat_interval_seconds`, optional `mqtt` (connect info). Registering the
same hardware again must send the current `device_token` in the body, and only to
the server that issued it.

### Enrollment API key (approval)

1. `POST /api/v1/agent/enroll/check` (body `{}`, also `GET`): the key is valid; which organization.
2. `POST /api/v1/agent/enroll/request` with inventory and optional `name`. Answer `201`: `request_id`,
   `claim_secret`, `status: pending`, `poll_interval_seconds`. Store both; they survive a reboot.
3. `POST /api/v1/agent/enroll/poll` with `{"request_id", "claim_secret"}` and no Authorization
   header, every `max(10, poll_interval_seconds)` seconds. `pending` keeps polling, `approved` carries
   the identity (exactly once), `rejected` and `expired` end the request. `429` with `Retry-After` slows down.

After success the agent stores the identity and starts working; nobody has to start it by hand.

## The heartbeat

`POST /api/v1/agent/heartbeat`, by default every 60 s; the answer's
`heartbeat_interval_seconds` sets the next interval. Body: inventory, `capabilities`,
and `software_report` when there is something to report. While a download runs, a
heartbeat may carry only `software_report` (progress).

The answer says that work waits: `wifi_pending` (collect from `GET /network/wifi`)
and `actions_pending` (collect from `GET /actions`). Desired software is listed by
`GET /software` on each cycle. Heartbeats never wait for deploy work.

### Capabilities

```json
{
  "systemd": true, "docker": true, "docker_compose": false, "wireguard": true, "ssh": true,
  "actions": ["reboot", "shutdown", "set_password", "add_user", "update_agent"],
  "deploy_kinds": ["systemd", "docker", "docker_compose"],
  "modules": {"heartbeat": {"state": "running", "restarts": 0}, "deploy": {"state": "parked", "restarts": 5, "last_error": "..."}}
}
```

- Booleans: the runtime or tool is installed on this device. The server skips a deployment
  whose runtime is reported `false`.
- `actions`, `deploy_kinds` (new in v1, additive): what this agent's code can do.
- `modules` (new in v1, additive, optional): module name to health (`running`, `starting`,
  `waiting`, `failed`, `parked`, `stopped`). The Go agent sends it; the Python agent has no modules.

The server stores the object as sent.

## Routes

All under `/api/v1/agent`. Request and answer schemas: `schemas/agent/http/<name>.json`.

| Method and path | Auth | Request | Answer |
| --- | --- | --- | --- |
| `POST /register` | token in body | `register.request` | `201` `register.response` |
| `POST /enroll/check` (also `GET`) | API key | `{}` or no body | `200` `enroll-check.response` |
| `POST /enroll/request` | API key | `enroll-request.request` | `201` `enroll-request.response` |
| `POST /enroll/poll` | claim secret in body | `enroll-poll.request` | `200` `enroll-poll.response` |
| `POST /heartbeat` | device | `heartbeat.request` | `200` `heartbeat.response` |
| `GET /mqtt` | device | | `200` `mqtt.response` |
| `POST /mqtt/credentials` | device | `{}` or no body | `200` `mqtt-credentials.response` |
| `GET /software` | device | | `200` `software.response` |
| `GET /signing-keys` | device | | `200` `signing-keys.response`; `404` = server does not sign |
| `GET /artifacts/{artifact_id}/download` | device | | `200` bytes, or `302` to storage |
| `POST /connectivity/wireguard` | device | `connectivity-wireguard.request` | `200` `connectivity-wireguard.response` |
| `GET /network/wifi` | device | | `200` `network-wifi.response` |
| `POST /network/wifi/report` | device | `network-wifi-report.request` | `200` `network-wifi-report.response` |
| `GET /actions` | device | | `200` `actions.response` |
| `POST /actions/report` | device | `actions-report.request` | `200` `actions-report.response` |
| `POST /applications/credentials` | device | `applications-credentials.request` | `201` `applications-credentials.response` |

`GET /api/v1/health` (no auth) is used to check that the server is reachable, for
example after joining a new Wi-Fi network.

### MQTT settings

On each cycle the agent compares `GET /mqtt` `config_version` with the version of its
stored MQTT config. When they differ it calls `POST /mqtt/credentials` (the old
password stops working) and reconnects. `enabled: false` stops the MQTT session.
`404` (an older server) keeps the stored config.

### Desired software and downloads

1. `GET /software` lists items (`workload_key`, `kind`, `release_*`, `artifact_id`, `revision`, `spec`,
   `artifact_sha256`, `key_id`, `signature`).
2. Signed items are verified before anything is downloaded: Ed25519 over
   `"meteorcloud-desired-v1\n"` + canonical JSON of the signed fields, with an organization key from
   `GET /signing-keys`, pinned on first use; a new key is trusted only when a pinned key endorsed it.
   See [docs/fleet/signed-updates.md](../docs/fleet/signed-updates.md).
3. `GET /artifacts/{id}/download` with `X-Meteorcli-Redirect: 1`. The answer is the bytes with
   `X-Checksum-SHA256`, or `302` with `Location` and `X-Checksum-SHA256`. The agent follows the redirect
   **without** the Authorization header, refuses `http` after `https`, and checks the SHA-256 (and the
   signed `artifact_sha256`).
4. Progress and results go in `software_report`: `sync_status` `updating` (with `progress_percent`),
   `in_sync`, `failed` (with `last_error`), `rolled_back`; `health` `unknown` / `healthy` / `unhealthy`.

Deploy kinds today: `systemd`, `docker`, `docker_compose`.

### One-off actions

`GET /actions` hands each action out once and may contain passwords. Kinds today:
`reboot`, `shutdown`, `set_password`, `add_user`, `update_agent`. Results go to
`POST /actions/report` as `done` or `failed` (+ `error`, at most 500 characters).
`reboot` and `shutdown` are reported `done` **before** they run. `update_agent` is
reported once the update has settled, on a later heartbeat.

### Wi-Fi

`GET /network/wifi` lists `apply` / `remove` changes, with passwords. A change with
`connect_now` is kept only if `GET /api/v1/health` answers on the new network, else the
old settings come back (`rolled_back`). `POST /network/wifi/report` sends `results`
(`applied`, `removed`, `rolled_back`, `failed`) and, when it changed or every few
minutes, `status`. Reports never contain a password.

### WireGuard

`POST /connectivity/wireguard` registers the device's public key (the private key never
leaves the device) and SSH facts; the answer has the tunnel settings and the SSH
gateway key or CA. When the agent starts trusting a CA it calls again at once with
`ssh_ca_public_key`. `404 connectivity_disabled` skips the tunnel; `403
connectivity_revoked` or `wireguard_key_changed` tears it down until an operator re-enrolls.

## MQTT

Client id `device-<device_id>`, username and password from the MQTT config, TLS
always. Only these topics; anything else is denied by the broker policy.

| Topic | Direction | QoS | Retained | Schema |
| --- | --- | --- | --- | --- |
| `devices/{id}/commands` | server to device (subscribe) | 1 | no | `mqtt/commands.json` |
| `devices/{id}/commands/result` | device to server | 1 | no | `mqtt/commands-result.json` |
| `devices/{id}/status` | device to server | 1 | **yes** | `mqtt/status.json` |
| `devices/{id}/metrics` | device to server, every 60 s | 1 | no | `mqtt/metrics.json` |
| `devices/{id}/events` | device to server | 1 | no | `mqtt/events.json` |

- **status:** `{"status": "online", "timestamp", "agent_version", "uptime_seconds"}` after every
  connect and once more about 2 s later; last will and a clean stop publish `{"status": "offline"}`.
- **commands:** `{"type": "ping", "command_id"}` is answered on `commands/result` with
  `{"command_id", "status": "completed", "result": {"message": "pong"}}`. `{"type": "sync_desired",
  "revision"}` is a hint that desired software changed; acting on it early is optional (the next cycle
  fetches `GET /software` anyway). Unknown types and unparsable messages are ignored.
- **metrics:** best-effort readings (`cpu_percent`, `memory_percent`, `disk_percent`, `temperature_c`,
  load, network rates, `wifi_signal_dbm`, `agent_errors`) plus flat app keys `app.<name>.<field>`.
- **events:** `{"type", "timestamp", ...}`: app lifecycle (`app.started`, `app.exited`,
  `app.restarted`, `app.oom_killed`, `app.healthy`, `app.unhealthy`, `app.crash_loop`,
  `app.removed`, all with `app`) and journal warnings (`log`, with `message`).

## Changing the contract

1. Change the schema under `schemas/agent/` and this page in the same PR. Request schemas carry the
   same length, range and count limits as the API's request models; a control-plane test fails when they differ.
2. Additive change: protocol stays 1. Breaking change: protocol 2, and the server must keep
   serving protocol 1 until no agent uses it.
3. Run `make backend-test` (validates the control plane) and `make agent-contract-test`
   (validates the agent); re-record with `AGENT_CONTRACT_MODE=record` when an exchange changed on purpose.
