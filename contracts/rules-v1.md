# Rule definition and engine contract, version 1

A rule is a JSON document. It is data, never code: every condition is a typed node
from a closed set, compiled once by the data plane's rule engine
(`src/data-plane/go/internal/rules`). Nothing in a rule can run a script, reach the
network or read another tenant's data.

- Machine-readable schema: [`rules-v1.schema.json`](rules-v1.schema.json). The Go
  validator (`internal/rules/dsl`) is the authority; the schema mirrors it for the
  control plane and the console.
- Examples that both are tested against: [`rules-v1.examples.json`](rules-v1.examples.json).

## Top level

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `schema_version` | integer | required | must be `1` |
| `name` | string, 1-200 | required | display name |
| `description` | string, max 2000 | | |
| `enabled` | boolean | `true` | |
| `severity` | `info` \| `warning` \| `critical` | `warning` | default severity of alerts |
| `scope` | object | required | which devices the rule applies to |
| `source.streams` | string[] (max 10) | `["meteor.device.metrics"]` | event streams the rule reads |
| `condition` | node | required | fire condition |
| `resolve` | node | | separate resolve condition (hysteresis); without it the alert resolves when `condition` is false |
| `cooldown` | duration | `0s` | after a resolve, no new alert for this long |
| `alerting.mode` | `single` \| `per_event` | `single` | `single`: at most one active alert per rule and device |
| `alerting.max_active` | integer 1-100 | 10 | cap of alert instances per device in `per_event` mode |
| `actions` | action[] (max 10) | one `alert` action | what to do on transitions |
| `late_tolerance` | duration | `60s` | how late an event may arrive and still update windows |

Unknown keys are rejected, so a typo never becomes a silently ignored setting.
Durations are Go duration strings: `"90s"`, `"5m"`, `"1h30m"`. Windows and
durations are between `1s` and `24h`.

## Scope

```json
{"type": "device", "device_ids": ["<uuid>", "..."]}
{"type": "group", "group_id": "<uuid>"}
{"type": "device_type", "device_type_id": "<uuid>"}
{"type": "organization"}
{"type": "platform"}
```

Every type accepts `labels`: an object of device labels that must all match
(`{"env": "production"}`). `platform` rules belong to no organization, apply to
every organization and are accepted only from platform administrators. An
organization rule only ever sees that organization's devices; the engine
re-checks the organization of every event against the device index.

## Condition nodes

| `type` | Fields | True when |
| --- | --- | --- |
| `comparison` | `field`, `operator`, `value` \| `values` \| `min`+`max` | see operators below |
| `all` | `conditions` (1-20) | every child is true (AND) |
| `any` | `conditions` (1-20) | some child is true (OR) |
| `not` | `condition` | the child is false |
| `duration` | `duration`, `condition` | the child has been true continuously for `duration`; meanwhile the rule is **pending** |
| `count_within` | `window`, `min_count`, `condition` | the child was true on at least `min_count` events within the last `window` |
| `consecutive` | `count`, `condition` | the child was true on `count` events in a row |
| `aggregate` | `function`, `field`, `window`, `operator`, `value` \| `min`+`max` | the function over the window compares true |
| `absence` | `duration`, optional `streams` | no matching event for `duration` |

Operators: `eq`, `ne` (number, string or boolean), `gt`, `gte`, `lt`, `lte`
(number), `between` (`min` and `max`, inclusive), `in`, `not_in` (`values`, up to
100), `exists`, `not_exists` (no value).

Aggregate functions: `avg`, `min`, `max`, `sum`, `count`, `delta` (newest minus
oldest sample in the window), `rate` (`delta` per second). Aggregates allow the
operators `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `between`.

"Remains false for a duration" is `duration` over `not`. Limits: nesting depth 8,
64 nodes per rule.

### Fields

`field` is a dotted path into the event payload: `{"net": {"eth0": {"rx": 5}}}`
gives `net.eth0.rx`. Arrays and nulls are not fields. A comparison on a field the
event does not carry keeps its previous outcome, because devices may report
metrics in separate messages.

### Time and order

Events carry event time. Windows and durations use event time; timers (pending
deadlines, absence, cooldown) run on the engine's clock. An event older than the
newest one seen for its device is *out of order*: it still lands in window
aggregates if it is within `late_tolerance`, but it cannot flip a comparison,
`duration` or `consecutive` condition backwards. Older than `late_tolerance`, it
is dropped and counted.

Window aggregates are kept in 60 slices per window, so a 10 minute window
resolves to 10 seconds and state stays small at any event rate.

## Alert lifecycle

The engine owns these phases per rule and device:

```
normal --condition true--> firing           (no duration: fires at once)
normal --duration started--> pending --held--> firing
pending --condition false--> normal
firing --resolve condition--> normal | cooldown --cooldown over--> normal
```

`acknowledged` and `silenced` are control-plane states of an alert record (user
actions); they never change what the engine evaluates.

Each firing starts a new *episode*. Every transition produces one outbox item per
matching action, keyed
`<rule_id>:<device_id>:<episode>:<transition>[:<fingerprint>][:<reason>]:a<index>`.
The key is stored uniquely, so a replayed evaluation can never enqueue an action
twice, and the control plane uses it to make alert creation and resolution
idempotent.

Resolve reasons: `condition_cleared`, `rule_updated` (the condition, scope,
sources or alerting mode changed: state is discarded and the alert re-fires if
still true), `rule_removed` (deleted or disabled), `device_out_of_scope`.

## Actions

| `type` | Fields | Runs on by default |
| --- | --- | --- |
| `alert` | `severity` | firing and resolved (always both) |
| `webhook` | `integration_id`, `params`, `timeout` | firing |
| `notification` | `integration_id`, `params` | firing |
| `command` | `command`, `params`, `timeout` | firing |
| `platform_event` | `params` | firing |

`on: ["firing", "resolved"]` overrides the default. Actions never run inside
evaluation: the engine writes them to the outbox in the same transaction as the
state change, and a separate dispatcher delivers them with retries, timeouts and a
dead letter table.

Outbox payload (one per action):

```json
{
  "rule_id": "…", "rule_version": 3, "rule_name": "High CPU",
  "organization_id": "…", "device_id": "…", "severity": "critical",
  "event": {"transition": "firing", "episode": 4, "at": "2026-11-13T12:00:00Z",
            "event_id": "…", "values": {"cpu_percent": 97.5}},
  "action": {"type": "webhook", "on": ["firing"], "integration_id": "…"}
}
```

## Snapshot (control plane to engine)

`GET /internal/rules/snapshot` returns every enabled rule at its current version
and the device index. The engine compiles it whole; a rule that fails validation is
skipped and reported, the rest load.

```json
{
  "version": "<etag>",
  "rules": [{"id": "…", "organization_id": "…", "version": 3, "definition": {…}}],
  "devices": [{"id": "…", "organization_id": "…", "group_id": "…", "device_type_id": "…",
               "labels": {"env": "production"}}]
}
```

`organization_id` is empty only for platform rules.

## Versioning

Every save of a rule creates a new immutable version. The engine compares a hash of
the parts that shape evaluation state (condition, resolve condition, scope,
sources, alerting mode). If the hash is unchanged (name, description, severity,
actions, cooldown edited) the state carries over; otherwise it is reset as
described under resolve reasons. A future `schema_version: 2` will be accepted
alongside version 1, never in place of it.
