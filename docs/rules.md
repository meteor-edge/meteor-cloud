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
| `GET` | `/rules/{id}` | `rule.read` | One rule |
| `PUT` | `/rules/{id}` | `rule.update` | Save a new version |
| `POST` | `/rules/{id}/enable`, `/disable` | `rule.update` | Switch without a new version |
| `DELETE` | `/rules/{id}` | `rule.delete` | Remove; open alerts resolve as `rule_removed`, history stays |
| `GET` | `/rules/{id}/versions` | `rule.read` | Every saved version, newest first |
| `GET` | `/rules/{id}/alerts` | `alert.read` | Alerts of one rule |
| `GET` | `/alerts?status=&active=&rule_id=&device_id=&severity=&before=&before_id=&limit=` | `alert.read` | Newest first; page with the last row's `fired_at` and `id` |
| `GET` | `/alerts/{id}` | `alert.read` | One alert with its history (`events`) |
| `POST` | `/alerts/{id}/acknowledge` | `alert.acknowledge` | Firing to acknowledged; repeating it is harmless |
| `GET`/`POST` | `/alert-silences` | `alert.read` / `alert.silence` | Silence by rule, device, both or neither until `ends_at` |
| `DELETE` | `/alert-silences/{id}` | `alert.silence` | End a silence |
| `GET`/`POST` | `/notification-integrations` | `rule.read` / `integration.manage` | Webhook targets; `secret` is sealed and never returned |
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
- **Commands.** A rule with a `command` action can only be saved by someone who may send
  that command: `reboot` and `shutdown` need `device.reboot`, others need
  `application.command`.
- **Acknowledged and silenced** are states of the alert record. They never change what
  the engine evaluates. A silence holds notifications; the alert is still recorded.

## Internal routes (data plane only)

Both need the `X-Data-Plane-Internal-Token` header (`DATA_PLANE_INTERNAL_TOKEN`).

- `GET /internal/rules/snapshot`: every enabled rule at its current version and the
  device index (enabled devices only). The `ETag` is a hash of the content;
  `If-None-Match` gives 304.
- `POST /internal/rules/alert-transitions`: `{"items": [...]}`, each an outbox payload
  of an `alert` action plus its `idempotency_key`. Answers one result per item:
  `applied`, `duplicate` (already applied: safe to retry) or `ignored` (unknown rule or
  device, or another tenant's), with `alert_id` and `silenced`.
