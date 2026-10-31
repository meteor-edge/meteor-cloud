# MQTT

Devices connect to EMQX over **one-way TLS** with a per-device username and password and a strict topic ACL.

```text
Registered device → authenticates to MQTT → publishes status/metrics → ping → pong
```

Certificate-based device authentication (mTLS) is not implemented. MQTT is optional and off by default in every deployment path. Enable it with `mqtt_enabled: true` (Ansible), the `mqtt` and `emqx` Compose profiles, or `mqtt.enabled=true` (Helm), using the bundled EMQX or an external one. Devices need TCP 8883 reachable.

## EMQX version and licensing

MeteorCloud ships the **bundled** broker as `emqx/emqx:5.8.6` (Compose and Helm). That image is the last Apache-2.0 open-source line. You can also point `MQTT_BROKER_HOST` at an **external** EMQX you run yourself.

| Question | Answer for MeteorCloud today |
| --- | --- |
| Can we self-host EMQX? | **Yes.** Bundled single-node EMQX on the cloud host (or your own broker) is the normal path. |
| Is 5.8.6 free to use in production? | **Yes**, under [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). Keep using 5.8.x under those terms indefinitely. |
| Does MeteorCloud need an EMQX commercial license for the bundled broker? | **Not for 5.8.6** as we ship it (one broker container / one Helm Deployment). |
| Can we upgrade the image past 5.8? | **Technically yes**, but **5.9.0+ is not Apache 2.0**. EMQ unified the product under [Business Source License (BSL) 1.1](https://www.emqx.com/en/content/license-faq). Do not bump the tag casually. |
| Free use of 5.9+? | Dev/test: free. **Single-node production**: free under BSL’s Additional Use Grant **if** you are not offering EMQX itself as a hosted service and not embedding it in a product you sell to third parties. **Multi-node clusters** and those commercial/hosted/embedded cases need an [EMQX commercial license](https://www.emqx.com/en/contact) or EMQX Cloud. |
| What about 5.8 maintenance? | EMQ’s open-source **5.8 line reached end-of-life on 2026-02-28** (no further OSS bug/security fixes from EMQ). Staying on 5.8.6 avoids the BSL change but means no upstream OSS patches. |

MeteorCloud’s Compose/Helm path runs **one** EMQX instance. That matches the free single-node grant if you ever move to 5.9+, but only if your overall product use still fits EMQ’s Additional Use Grant. If you sell MeteorCloud (or MQTT connectivity) as a commercial hosted/embedded offering, review the [EMQX Licensing FAQ](https://www.emqx.com/en/content/license-faq) and talk to EMQ before upgrading past 5.8. This page is operator guidance, not legal advice.

Pin the broker in:

- Compose: `deploy/compose/data-plane.yml` → `image: emqx/emqx:5.8.6`
- Helm: `deploy/kubernetes/helm/meteorcloud/values.yaml` → `mqtt.emqx.image.tag`

Other bundled components (Postgres, Redis, Traefik, MinIO, …): [Licensing](../licensing.md).

## Security model

| Control | What it does |
| --- | --- |
| Per-device MQTT password | Each device has `device_<device_id>` / a random secret. The platform stores only the SHA-256 hash. The plaintext is returned **once** at registration/claim. |
| Broker TLS (`mqtts://localhost:8883`) | Protects the password in transit. Devices verify `ca.crt`. |
| Topic ACL | A device may publish only `status`, `metrics`, and `commands/result`, and subscribe only to `commands`. The platform MQTT user is a superuser for the Fleet MQTT test console. |
| Revoke / disable | MQTT auth rejects disabled devices and revoked MQTT credentials (`POST .../devices/{id}/mqtt/revoke`). |
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
- EMQX dashboard (dev only): http://localhost:18083 (`admin` / `public`)
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
restart EMQX after generating certs so it loads the new files.

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
  -u device_DEVICE_ID -P MQTT_PASSWORD \
  -t devices/DEVICE_ID/commands
```

Publish status:

```bash
mosquitto_pub \
  -h localhost -p 8883 --cafile certs/ca.crt \
  -u device_DEVICE_ID -P MQTT_PASSWORD \
  -t devices/DEVICE_ID/status \
  -m '{"status":"online"}'
```

A second device username must be **denied** on `devices/OTHER_ID/#`.

## Topics

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
