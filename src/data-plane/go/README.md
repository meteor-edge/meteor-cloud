# Application data plane (Go)

One binary, `cmd/dataplane`: authentication from a policy snapshot, ingestion with a bounded queue, storage in ClickHouse (data) and PostgreSQL (commands), live fan-out over WebSocket, historical queries and command delivery.

- What it is and why it is built this way: [docs/data-plane](../../../docs/data-plane/README.md)
- Protocol: [contracts/data-plane-v1.md](../../../contracts/data-plane-v1.md), [api/openapi.yaml](api/openapi.yaml)
- Settings, metrics, troubleshooting: [docs/data-plane/operations.md](../../../docs/data-plane/operations.md)

```bash
go vet ./... && go test -race ./...

# storage conformance tests against a real PostgreSQL and a real ClickHouse
DATAPLANE_TEST_DATABASE_URL=postgres://user:pass@localhost:5432/dataplane_test \
DATAPLANE_TEST_CLICKHOUSE_URL=http://localhost:8123 DATAPLANE_TEST_CLICKHOUSE_PASSWORD=... \
  go test -race ./internal/storage/

# run without any infrastructure (see sdk/python/README.md for a policy file)
DATAPLANE_STORAGE=memory DATAPLANE_BUS=memory DATAPLANE_POLICY_FILE=policy.json go run ./cmd/dataplane
```

| Package | Role |
| --- | --- |
| `internal/identity` | Policy snapshot: applications, permissions, credential hashes. Pulled, never pushed |
| `internal/streams` | Stream names and permission patterns |
| `internal/envelope` | Message validation and the stored envelope |
| `internal/ingest` | Bounded queue, batching, retries, backpressure |
| `internal/storage` | `Store` and `EventStore` interfaces; ClickHouse (events), PostgreSQL and in-memory implementations |
| `internal/bus` | Fan-out between replicas: EMQX or in-memory. MQTT topics and ACLs |
| `internal/live` | Live subscriptions |
| `internal/commands` | Command state machine, redelivery, timeouts |
| `internal/api` | HTTP and WebSocket API, rate limits, MQTT login exchange |
| `internal/mqttgw` | Ingestion of messages published directly over MQTT; dead letters |
| `internal/observability` | Prometheus metrics, JSON logging |

## Rule engine (`internal/rules`)

A stateful rule engine for device telemetry: thresholds, durations, windows,
rates, missing telemetry, hysteresis and cooldowns, with an alert lifecycle and an
action outbox. Rule format and semantics: [`contracts/rules-v1.md`](../../../contracts/rules-v1.md).

| Package | Role |
| --- | --- |
| `rules` | `Processor`: converts `envelope.Event` and calls the engine (the only envelope-aware code) |
| `rules/dsl` | rule definition, strict parsing, validation, condition hash |
| `rules/eval` | compiled condition tree; pure, deterministic evaluation with persisted node state |
| `rules/state` | durable records and `Store`: memory (tests) and PostgreSQL schema `rules` |
| `rules/engine` | candidate rules, dedup, ordering, alert state machine, group commit, timers, outbox, rule simulation |

The engine does not depend on any broker or queue. It is wired into the rule
processor consumer in a follow-up change; until then it is a library with tests.

```bash
# unit tests (fake clock, memory store)
go test -race ./internal/rules/...
# plus the PostgreSQL store and multi-worker tests
RULES_TEST_DATABASE_URL=postgres://user:pass@localhost:5432/db go test -race ./internal/rules/...
```
