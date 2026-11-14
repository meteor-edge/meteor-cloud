# MQTT

Devices connect to the MQTT broker over **one-way TLS** with a per-device username and password and a strict topic ACL.

```text
Registered device → authenticates to MQTT → publishes status/metrics → ping → pong
```

Certificate-based device authentication (mTLS) is not implemented. MQTT is optional and off by default in every deployment path. Enable it with `mqtt_enabled: true` (Ansible), the `mqtt` and `mosquitto` Compose profiles, or `mqtt.enabled=true` (Helm), using the bundled broker or an external one. Devices need TCP 8883 reachable.

## Choosing a broker

The bundled broker is **Eclipse Mosquitto** by default. **EMQX 5.8.6** and **VerneMQ** are selectable with one setting (`MQTT_PROVIDER` and the Compose profile of the same name). All three ask the control plane for every login and topic permission, so the rules below are identical whichever one runs.

How they differ (licence, clustering, patches), how each is wired, TLS, and how to switch: [MQTT brokers](mqtt-brokers.md). Licences: [Licensing](../licensing.md).

## Security model

| Control | What it does |
| --- | --- |
| Per-device MQTT password | Each device has `device_<device_id>` / a random secret. The platform stores only the SHA-256 hash. The plaintext is returned **once** at registration/claim. |
| Broker TLS (`mqtts://localhost:8883`) | Protects the password in transit. Devices verify `ca.crt`. |
| Topic ACL | A device may publish only `status`, `metrics`, and `commands/result`, and subscribe only to `commands`. The platform MQTT user is a superuser for the Fleet MQTT test console. |
| Revoke / disable | Disabled devices and revoked MQTT credentials (`POST .../devices/{id}/mqtt/revoke`) are refused at login and on every publish and subscribe. Mosquitto also disconnects the live session at once. |
| Client ID | A device must connect as `device-<device_id>` or `device-<device_id>-<suffix>`, so it cannot take over another device's session. |
| Retained messages | A device may retain only its own `status`. |
| HTTP vs MQTT | The `dev_` HTTP token and the MQTT password are separate secrets. HTTP last-seen and MQTT online/offline are independent. |

Internal broker callbacks:

```text
POST /internal/mqtt/authenticate
POST /internal/mqtt/authorize
POST /internal/mqtt/ingest
```

These require `X-MQTT-Internal-Token` and are not user APIs. The data plane
forwards every inbound MQTT payload to ingest. Last-value status/metrics stay
in PostgreSQL (`TELEMETRY_PROVIDER=postgresql`). See `contracts/mqtt-http.md`.

## Local setup

```bash
make mqtt-certs   # or make dev, which generates certs if missing
make dev
```

- MQTT TLS: `mqtts://localhost:8883`
- Port `1883` is not exposed.

Then register a device (`meteorcli register` or request-token/claim). The agent
stores MQTT credentials in `mqtt.json` (`0600`) and `mqtt-ca.crt`. Start
`meteorcli run` so the agent connects, publishes `online` plus a metrics snapshot,
and answers ping.

On the device detail page: **Test Connection** (ping), latest CPU/memory/disk
snapshot, topics, and meteorcli examples. The organization MQTT test page
(`/organizations/{id}/mqtt`) is still available by URL for a free-form
topic/payload console (platform credentials; any topic).

To publish a status probe from the Pi:

```bash
meteorcli mqtt-test
```

That publishes one JSON message to `devices/{device_id}/status` over TLS.
Register/claim must have written `~/.config/meteorcli/mqtt.json` first
(`meteorcli status` should say `MQTT: configured`). `MQTT_PUBLIC_HOST` on the
server must be reachable from the device (not `localhost` for a Pi on the LAN).
`make mqtt-certs` includes this machine's LAN IP in the broker certificate;
restart the broker after generating certs so it loads the new files.

To print inbound commands:

```bash
meteorcli mqtt-listen
```

That defaults to `devices/{device_id}/commands` (the only device subscribe).

```bash
meteorcli mqtt-test
meteorcli mqtt-test devices/DEVICE_ID/status '{"status":"online"}'
meteorcli mqtt-test metrics
meteorcli mqtt-listen
meteorcli mqtt-listen commands
```

Wildcards and other devices' topics are rejected. Uses a separate MQTT client
id, so `meteorcli run` can stay connected.

## Manual mosquitto checks

Subscribe (as the device):

```bash
mosquitto_sub \
  -h localhost -p 8883 --cafile certs/ca.crt \
  -i device-DEVICE_ID-sub -u device_DEVICE_ID -P MQTT_PASSWORD \
  -t devices/DEVICE_ID/commands
```

Publish status:

```bash
mosquitto_pub \
  -h localhost -p 8883 --cafile certs/ca.crt \
  -i device-DEVICE_ID-pub -u device_DEVICE_ID -P MQTT_PASSWORD \
  -t devices/DEVICE_ID/status \
  -m '{"status":"online"}'
```

The broker rejects a device whose client id is not `device-DEVICE_ID` or
`device-DEVICE_ID-<suffix>`. A second device username must be **denied** on
`devices/OTHER_ID/#`.

## Topics

Application data uses a separate, versioned hierarchy under `meteor/v1/orgs/{org}/...`, owned by the application data plane. Applications normally use the SDK and never connect to the broker; those that do log in with a short-lived token (username `app_...`) that EMQX verifies with its JWT authenticator. See [data plane protocol → MQTT](../../contracts/data-plane-v1.md#mqtt). The topics below are the device management topics.

```text
devices/{device_id}/status              PUBLISH (device, LWT) / platform subscribe
devices/{device_id}/metrics             PUBLISH (device) / platform subscribe (latest snapshot only)
devices/{device_id}/commands            SUBSCRIBE (device) / PUBLISH (platform)
devices/{device_id}/commands/result     PUBLISH (device) / platform subscribe
devices/{device_id}/telemetry           reserved; devices are denied
```

The Fleet MQTT test page uses the **platform** MQTT user and may publish or
subscribe to other names (including `events`). Devices cannot.

QoS 1 for status, metrics, commands, and results. Metrics are not time-series:
the backend keeps only the latest snapshot. Disk % is for `/`. Temperature is
omitted when `/sys/class/thermal/thermal_zone0/temp` is missing.
