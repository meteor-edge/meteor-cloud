"""Platform history: control-plane changes recorded as events in the data plane (ClickHouse).

PostgreSQL keeps the current state (a device's status in a Deploy). Every change
of that state is also written, in the same transaction, to an outbox table; a
relay sends the outbox to the data plane, which stores it next to device
telemetry. The control plane never talks to ClickHouse itself.
"""
