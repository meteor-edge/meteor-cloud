# MeteorCloud data plane

> **Replaced.** The `data-plane` service is now the Go MQTT forwarder
> ([`go/cmd/mqtt-forwarder`](go/cmd/mqtt-forwarder/main.go), [docs](../../docs/data-plane/mqtt-forwarder.md)).
> This Python gateway stays for one release as a fallback (Compose profile `mqtt-python`,
> Ansible `data_plane_gateway: python`, Helm `dataPlane.gateway: python`), then it is removed.

Python process that owns the platform MQTT session to the broker (Mosquitto, EMQX, or VerneMQ). It publishes and
subscribes on behalf of the control plane, then POSTs each inbound payload to
`CONTROL_PLANE_URL/internal/mqtt/ingest`.

## Run

```bash
export CONTROL_PLANE_URL=http://127.0.0.1:8000
export MQTT_BROKER_HOST=127.0.0.1
uvicorn data_plane.main:app --host 0.0.0.0 --port 8081
```

See `contracts/mqtt-http.md` for the HTTP JSON contract. Telemetry storage stays
in the control plane (`TELEMETRY_PROVIDER=postgresql`).
