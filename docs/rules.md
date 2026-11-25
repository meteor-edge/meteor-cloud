# Rules and alerts

A rule watches device data and opens an alert (and runs actions) when its condition
holds. Rules are JSON documents in a closed format, never code:
[contracts/rules-v1.md](../contracts/rules-v1.md) describes every field, with
[examples](../contracts/rules-v1.examples.json).

Who does what:

- **Control plane** (this API) stores rules and every version of them, checks who may
  change them, keeps the alert records people act on, silences, and notification
  integrations.
- **Rule engine** (data plane, `src/data-plane/go/internal/rules`) evaluates events, keeps
  per-rule-and-device state in PostgreSQL schema `rules`, and writes transitions to an
  outbox. A dispatcher delivers them: `alert` actions come back to the control plane,
  webhooks go out with retries.

## API

Prefix: `/api/v1/organizations/{organization_id}`. Every change is written to the
[audit log](audit-log.md) (`rule.*`, `alert.*`, `integration.*`).

| Method | Path | Permission | What it does |
| --- | --- | --- | --- |
| `GET` | `/rules?enabled=&limit=&offset=` | `rule.read` | List rules (current definition included) |
| `POST` | `/rules` | `rule.create` | Create from `{"definition": {...}}`; version 1 |
| `POST` | `/rules/validate` | `rule.read` | Check a definition without saving: `{valid, problems[], condition_hash}` |
| `POST` | `/rules/preview-devices` | `rule.read` | Devices the definition's scope selects now (first 200 and the total) |
| `POST` | `/rules/test` | `rule.read` | Run a definition over sample events (`{"definition", "events": [{"time", "data"}], "until"}`) in the engine with a virtual clock; every step comes back. Nothing is stored and no action runs |
| `GET` | `/rules/metrics?search=` | `rule.read`, `device.read` | Metrics rules can use: name, unit, how many devices report each and the range of current readings |
| `POST` | `/rules/live-devices` | `device.read` | Devices by their last readings (`conditions`, `match` `all`/`any`), connection, group, type and active alerts, with or without a rule. Readings older than `fresh_minutes` (5) do not match |
| `GET` | `/rules/{id}/status?after=&limit=` | `rule.read` | The engine's state of the rule per device: phase, episode, last values, next timer |
| `GET` | `/rules/{id}` | `rule.read` | One rule |
| `PUT` | `/rules/{id}` | `rule.update` | Save a new version |
| `POST` | `/rules/{id}/enable`, `/disable` | `rule.update` | Switch without a new version |
| `DELETE` | `/rules/{id}` | `rule.delete` | Remove; open alerts resolve as `rule_removed`, history stays |
| `GET` | `/rules/{id}/versions` | `rule.read` | Every saved version, newest first |
| `GET` | `/rules/{id}/alerts` | `alert.read` | Alerts of one rule |
| `GET` | `/alerts?status=&active=&rule_id=&device_id=&severity=&search=&metric=&before=&before_id=&limit=` | `alert.read` | Newest first, with device names; `search` matches device or rule name, `metric` the triggering metric; page with the last row's `fired_at` and `id` |
| `GET` | `/alerts/summary` | `alert.read` | Active alerts by status, severity and rule |
| `GET` | `/alerts/{id}` | `alert.read` | One alert with its history (`events`) |
| `POST` | `/alerts/{id}/acknowledge` | `alert.acknowledge` | Firing to acknowledged; repeating it is harmless |
| `GET`/`POST` | `/alert-silences` | `alert.read` / `alert.silence` | Silence by rule, device, both or neither until `ends_at` |
| `DELETE` | `/alert-silences/{id}` | `alert.silence` | End a silence |
| `GET`/`POST` | `/notification-integrations` | `rule.read` / `integration.manage` | Channels: `email` (`recipients`), `telegram` (`chat_id`, bot token as `secret`), `slack` and `webhook` (`url`); `secret` is sealed and never returned; `url` is sealed and complete only for `integration.manage`, others see its host |
| `GET` | `/notification-integrations/capabilities` | `rule.read` | Whether the engine answers and has a mail server |
| `POST` | `/notification-integrations/{id}/test` | `integration.manage` | Send a sample alert now; answers whether it arrived |
| `PATCH`/`DELETE` | `/notification-integrations/{id}` | `integration.manage` | Change; `"secret": ""` removes it. A target a rule uses cannot be deleted |

Validation errors answer 422 with every problem in the message, for example
`severity: must be info, warning or critical; scope.type: platform rules are for platform administrators only`.

```bash
curl -X POST "$API/api/v1/organizations/$ORG/rules" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"definition": {
    "schema_version": 1, "name": "High CPU", "severity": "critical",
    "scope": {"type": "organization", "labels": {"env": "production"}},
    "condition": {"type": "duration", "duration": "5m",
      "condition": {"type": "comparison", "field": "cpu_percent", "operator": "gt", "value": 90}}}}'
```

## Console

Under **Monitoring** in the sidebar:

- **Device health**: the latest readings of every device. Quick filters (CPU or memory
  above 80%, disk, temperature, offline, with active alerts) and filters on any metric;
  *Create rule from this filter* opens the editor with it.
- **Alerts**: active alerts by default with counts by status and severity, filters for
  rule, device, metric, severity and text, acknowledge, and silences (2 hours from a row,
  or 1 hour to 1 week, end early).
- **Rules**: every rule in words, with its active alerts and an on/off switch.
- **Rule editor**: a form (templates, device scope, searchable metrics, conditions, how
  long it must last, channels) or the same rule as JSON, switchable at any time. The form
  shows which devices are over the limits right now. A rule the form cannot show in full
  (for example nested conditions) is edited as JSON, so nothing is ever dropped. Also
  *Validate*, *Preview devices*, *Run test* (sample events through the engine with a
  virtual clock), the status per device, and earlier versions.
- **Notifications**: email, Telegram, Slack and webhook channels with *Send test*.

Members limited to device groups see only those devices, their metrics and their alerts.
Viewers can read everything; changing rules and channels needs the developer,
admin or owner role, and acknowledging or silencing also works for operators.

## Email and Telegram

- **Email** goes through the platform's mail server (`RULES_SMTP_*`, off by default).
  Organizations only choose recipients (up to 20, visible to each other).
- **Telegram** uses each organization's own bot: create one with `@BotFather`, send it a
  message or add it to a group, and read the chat id from
  `https://api.telegram.org/bot<token>/getUpdates`. The token is stored encrypted, never
  returned and never logged. Messages (device and rule name, readings) are sent to
  Telegram's servers.
- **Grouping**: email, Telegram and Slack messages wait 10 seconds, and all devices the
  same rule fired (or resolved) for by then share one message: the count and the first 20
  names, up to 1,000 devices. Devices under a silence are left out. Webhooks go to programs
  and stay one request per alert, and the alerts page always has one alert per device.
- Each channel is delivered and retried on its own: one that is down does not delay the
  others. Within a channel, *resolved* never overtakes *firing*.
- Disabling or deleting a rule resolves its alerts without notifying its channels.

## Rules of the road

- **Versions.** Every save adds an immutable version. If only the name, description,
  severity, actions or cooldown changed, the engine keeps its state. If the condition,
  scope, sources or alerting mode changed, the state resets and an open alert resolves
  as `rule_updated` (and fires again if the condition still holds).
- **Tenant isolation.** Scope references (devices, group, device type) and integrations
  must belong to the organization. The engine checks every event's organization again,
  and the control plane ignores any engine message whose rule or device belongs to
  another organization.
- **Platform rules** (scope `platform`) exist in the data model but are not accepted from
  this API until there is a platform administrator role.
- **Commands.** Rules can send `reboot` and `shutdown`. Saving such a rule needs
  `device.reboot`, and every delivery re-checks that the author still has it for that
  device (including device-group scope).
- **Members limited to device groups** manage only rules whose scope is one of their
  groups or devices in them, and preview, acknowledge and silence only their devices. A
  silence without a device needs organization-wide access.
- **Duplicates.** The engine remembers the last 64 event ids per device. An event that is
  redelivered after more than 64 newer ones of the same device can count twice.
- **Acknowledged and silenced** are states of the alert record. They never change what
  the engine evaluates. A silence holds notifications; the alert is still recorded.

## Running it

The engine runs inside the `rule-processor` service (Compose profile `nats`, see
[JetStream](data-plane/jetstream.md)). Settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `RULES_DATABASE_URL` | Compose: the platform PostgreSQL | Where schema `rules` lives (created on start). Empty: events are only counted |
| `CONTROL_PLANE_URL`, `DATA_PLANE_INTERNAL_TOKEN` | | Snapshot, alert records, deliveries |
| `RULES_SNAPSHOT_INTERVAL` | `15s` | How often the rule snapshot is checked (ETag, so cheap) |
| `RULES_API_ADDR` | `:8095` | Internal API for rule tests and status; the control plane reaches it at `RULES_ENGINE_URL` |
| `RULES_DISPATCH` | `true` | Deliver actions in this process (`false` to run delivery elsewhere) |
| `RULES_DISPATCH_WORKERS`, `RULES_DISPATCH_MAX_ATTEMPTS`, `RULES_DISPATCH_BACKOFF` | `16`, `10`, `5s,30s,2m,10m,30m` | Delivery concurrency and retries |
| `RULES_WEBHOOK_ALLOW_PRIVATE` | `false` | Allow webhooks to private and loopback addresses (LAN-only installs) |
| `RULES_SMTP_HOST`, `RULES_SMTP_PORT`, `RULES_SMTP_FROM` | empty, `587`, empty | Mail server and sender for email channels |
| `RULES_SMTP_USERNAME`, `RULES_SMTP_PASSWORD` | | Mail login (`RULES_SMTP_PASSWORD_FILE` also works) |
| `RULES_SMTP_TLS` | `starttls` | `starttls` (required, never downgraded), `tls` or `none` (no login allowed) |
| `RULES_TELEGRAM_API_URL` | `https://api.telegram.org` | Bot API server |
| `RULES_CONSOLE_URL` | empty | Adds a link to the alerts page to messages |
| `RULES_NOTIFY_GROUP_WAIT` | `10s` | How long messages wait for other devices to join (see grouping); `0s` sends at once |
| `RULES_COMMIT_BATCH`, `RULES_COMMIT_LINGER`, `RULES_TIMER_INTERVAL`, `RULES_TIMER_WORKERS` | `64`, `2ms`, `1s`, `16` | Group commit and timer tuning |

Any number of replicas can run: per-device locks plus compare-and-swap on the state
rows keep evaluations consistent, timers and outbox rows are claimed with leases
(`FOR UPDATE SKIP LOCKED`), and idempotency keys make every replay harmless. Failed
deliveries land in `rules.action_dead_letters` with the last error.

Metrics (on the ops port, `:8094/metrics`): `meteorcloud_rules_*` for evaluation,
transitions, timers, commits and refused rules, plus
`meteorcloud_rules_actions_dispatched_total{type,result}` and
`meteorcloud_rules_action_delivery_seconds`.

## Internal routes (data plane only)

Both need the `X-Data-Plane-Internal-Token` header (`DATA_PLANE_INTERNAL_TOKEN`).

- `GET /internal/rules/snapshot`: every enabled rule at its current version and the
  device index (enabled devices only). The `ETag` is a hash of the content;
  `If-None-Match` gives 304.
- `POST /internal/rules/alert-transitions`: `{"items": [...]}`, each an outbox payload
  of an `alert` action plus its `idempotency_key`. Answers one result per item:
  `applied`, `duplicate` (already applied: safe to retry) or `ignored` (unknown rule or
  device, or another tenant's), with `alert_id` and `silenced`.
- `POST /internal/rules/deliveries`: URL, kind, settings and secret of an integration for one
  webhook, only for the integration's own organization, and whether a silence holds it.
- `POST /internal/rules/test-deliveries`: the same for a test message of an integration
  (no rule, no silence). The engine's test route only takes the integration's id.
- `POST /internal/rules/commands`: send a rule's `reboot` or `shutdown` as the author of that rule version
  (403 when the author lost `device.reboot` for that device).
- `POST /internal/rules/platform-events`: record a `rule.event` audit row (once per key).

The rule processor serves `POST /internal/rules/simulate`, `POST /internal/rules/test-delivery`,
`GET /internal/rules/status/{rule_id}` and `GET /internal/rules/engine` on
`RULES_API_ADDR`, with the same token.
