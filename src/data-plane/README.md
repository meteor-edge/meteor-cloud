# MeteorCloud data plane

Python process that owns the EMQX platform MQTT session. It publishes and
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
