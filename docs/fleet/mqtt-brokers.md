# MQTT brokers

MeteorCloud treats the MQTT broker as replaceable infrastructure. Three brokers are supported behind one integration, and the choice is one setting.

| | Mosquitto (default) | EMQX 5.8.6 | VerneMQ |
| --- | --- | --- | --- |
| `MQTT_PROVIDER` / Compose profile | `mosquitto` | `emqx` | `vernemq` |
| Licence, commercial use | EPL-2.0 / EDL-1.0, free | Apache 2.0, free. **5.9+ is BSL: needs an EMQ licence** | Source Apache 2.0. **Official image is under the VerneMQ EULA: needs a subscription, or build from source** |
| Security patches | Yes (Eclipse, Debian) | **None since 2026-02-28** (5.8 open-source line ended) | Yes |
| Clustering | No, one node | Yes (5.8.6) | Yes (see [VerneMQ cluster](#vernemq-cluster)) |
| How it asks the control plane | `meteorcloud_auth` plugin | HTTP authn/authz | `vmq_webhooks` |
| Drops a revoked device's live session | Immediately | At next reconnect | At next reconnect |
| Bundled in | Compose, Ansible, Helm | Compose, Ansible, Helm | Compose, Ansible. Helm: external only |

Full licence table: [Licensing](../licensing.md). This page is operator guidance, not legal advice.

## Why the broker is separate

| Part | Owns |
| --- | --- |
| Control plane | Device identity, credentials, organizations, **the topic policy**, revocation |
| Broker | MQTT connections, sessions, routing. It holds **no** users, passwords, or ACL files |
| Data plane | The platform MQTT session: publishes commands, forwards device messages to the control plane |
| Device agent (`meteorcli`) | Connects with the host, port, CA, username, and password it got at registration |

The broker never decides anything itself. For every login, publish, and subscribe it asks the control plane, which answers from one policy (`src/control-plane/app/mqtt/policy.py`). Because nothing is copied into the broker:

- Issuing, rotating, or revoking a credential is one database write. There is no "database updated but broker not" state to repair, and the operations are idempotent.
- Switching broker needs no data migration. Devices keep their credentials.
- If the control plane is unreachable, brokers deny (fail closed).

```text
device ──TLS 8883──► broker ──HTTP (private network)──► control plane
                       │   "may device_<id> log in / publish X / subscribe Y?"
                       │◄── allow | deny  (from policy.py + device state)
                       ▼
                  data plane (platform user) ──HTTP──► control plane ingest
```

## Broker adapter interface

Services depend on two small ports, never on a broker:

| Port | File | Used for |
| --- | --- | --- |
| `MQTTGateway` | `app/ports/mqtt.py` | Publish and watch topics (through the data-plane HTTP API) |
| `MqttBroker` | `app/ports/mqtt_broker.py` | Per-broker behaviour: `disconnect_device()`, `instant_disconnect`, `clustering` |

Adapters live in `app/adapters/mqtt_brokers.py` (`MosquittoBroker`, `EmqxBroker`, `VerneMQBroker`) and are selected by `MQTT_PROVIDER`.

What the usual "broker adapter" operations map to here:

| Operation | Where it happens |
| --- | --- |
| Provision / rotate device credentials | `issue_mqtt_credentials()` writes the hash to PostgreSQL. Nothing is pushed to the broker |
| Revoke credentials | `revoke_mqtt_credential()` / disable / delete, then `MqttBroker.disconnect_device()` |
| Publish/subscribe permissions | `app/mqtt/policy.py` (rules are data), asked by the broker per request |
| Connection details for a device | Returned at registration and by `GET /api/v1/agent/mqtt/credentials` |
| Broker health | Data plane `GET /health` and its `connected` metric; each broker container has a health check |

## Authorization flow and topic model

Topics are `devices/{device_id}/...`. Device IDs are UUIDs that are unique across all organizations, so a rule that pins a device to its own ID also isolates tenants.

| Who | Action | Topic | Result |
| --- | --- | --- | --- |
| Device `device_<id>` | publish | `devices/<own id>/status` (may be retained), `/metrics`, `/commands/result` | allow |
| Device | subscribe / receive | `devices/<own id>/commands` | allow |
| Device | publish | `devices/<own id>/commands` | deny |
| Device | anything | another device's topics, wildcards, `$SYS`, `$share`, `meteorcloud/control/...` | deny |
| Platform user (data plane) | anything | anything | allow |
| Anyone else, anonymous | anything | anything | deny |

Also enforced:

- **Client ID.** A device must connect as `device-<own id>` or `device-<own id>-<suffix>` (what `meteorcli` uses). This stops one device from taking over another device's session.
- **Identity comes from the login**, never from a device ID inside a payload. A command result is applied only if the command belongs to the device whose topic it arrived on.
- **Revoked or disabled devices** are refused at login and on every publish and subscribe, even on a session that is still open. Mosquitto also disconnects them at once.
- `devices/<id>/events` is reserved for later device events and is denied until a rule opens it.

Tests: `src/control-plane/tests/test_mqtt_policy.py` (policy, cross-tenant, VerneMQ hooks), the plugin's Go tests, and `tests/mqtt_live/` against a real broker (`make test-mqtt`).

## Run each broker locally

Set both the Compose profile and `MQTT_PROVIDER` in `deploy/compose/.env`, then `make dev`.

```bash
# Mosquitto (default)
COMPOSE_PROFILES=postgres,redis,minio,mqtt,mosquitto
MQTT_PROVIDER=mosquitto

# EMQX 5.8.6
COMPOSE_PROFILES=postgres,redis,minio,mqtt,emqx
MQTT_PROVIDER=emqx

# VerneMQ (you accept the VerneMQ EULA for the official image)
COMPOSE_PROFILES=postgres,redis,minio,mqtt,vernemq
MQTT_PROVIDER=vernemq
VERNEMQ_ACCEPT_EULA=yes
```

Run only one broker profile at a time: they all publish port 8883 and answer to the same network name (`mqtt-broker`).

Live security tests against the chosen broker:

```bash
make test-mqtt                                             # Mosquitto
MQTT_PROVIDER=emqx make test-mqtt
MQTT_PROVIDER=vernemq VERNEMQ_ACCEPT_EULA=yes make test-mqtt
```

Ansible: `mqtt_provider: mosquitto | emqx | vernemq` (plus `vernemq_accept_eula: "yes"`). Helm: `mqtt.provider`.

## Mosquitto

Image `meteorcloud/mosquitto` is built from `src/data-plane/broker/mosquitto/`: Debian's Mosquitto plus our `meteorcloud_auth` plugin (Go, no third-party modules). The broker config is baked in (`mosquitto.conf`): TLS-only listener on 8883, no anonymous clients, 256 KB packet limit, persistence under `/mosquitto/data`.

| Setting (environment) | Meaning | Default |
| --- | --- | --- |
| `CONTROL_PLANE_URL` | Where the plugin asks | `http://backend:8000` |
| `MQTT_INTERNAL_TOKEN` | Shared secret. Empty stops the broker at startup | none |
| `MQTT_ACL_CACHE_SECONDS` | Reuse an "allowed" topic decision (0 = ask every time) | `60` |
| `MQTT_ACL_DENY_CACHE_SECONDS` | Reuse a "denied" decision | `10` |
| `MQTT_AUTH_TIMEOUT_MS` | HTTP timeout per question | `3000` |

Logins are never cached. When a device is revoked, disabled, or deleted, the control plane publishes to the platform-only topic `meteorcloud/control/disconnect`; the plugin drops that device's sessions and its cached decisions.

One node only. On one 4-core machine a single node carried 20,000 QoS 1 messages per second from 5,000 TLS clients with no loss at under 10% of one core; that is a lab number, not a guarantee. To go past one node, run one broker per customer or group, or switch to a clustering broker.

## EMQX

Unchanged from earlier releases: `emqx/emqx:5.8.6` with HTTP authentication and authorization against `/internal/mqtt/authenticate` and `/authorize`. It now also sends the client ID and the retain flag so the same rules apply.

One difference: EMQX checks the topic behind a `$share/<group>/` filter, so a device may share-subscribe to its own command inbox (it still cannot reach any other topic). Mosquitto and VerneMQ refuse every `$share` filter from a device.

Stay on 5.8.6 unless you have an EMQ commercial licence: 5.9.0 and later are under BSL 1.1, which does not allow hosting it for others, embedding it in a product you sell, or clustering without a licence ([EMQX Licensing FAQ](https://www.emqx.com/en/content/license-faq)). The 5.8 open-source line gets no fixes since 2026-02-28.

## VerneMQ

VerneMQ asks the control plane through `vmq_webhooks`. The control plane has a thin translation layer (`app/mqtt/vernemq_router.py`) for VerneMQ's request and answer format; the decisions are the same policy.

| Hook | Endpoint |
| --- | --- |
| `auth_on_register`, `auth_on_register_m5` | `POST /internal/mqtt/vernemq/auth_on_register[_m5]?token=<MQTT_INTERNAL_TOKEN>` |
| `auth_on_publish`, `auth_on_publish_m5` | `POST /internal/mqtt/vernemq/auth_on_publish[_m5]?token=...` |
| `auth_on_subscribe`, `auth_on_subscribe_m5` | `POST /internal/mqtt/vernemq/auth_on_subscribe[_m5]?token=...` |

- VerneMQ webhooks cannot send custom headers, so the shared secret is in the URL. Keep that URL on the private network between broker and control plane, and do not log it at a proxy.
- The control plane sends no `cache-control` header, so VerneMQ asks for every publish and subscribe. A revoked device loses access at once; its open connection stays until it reconnects.
- `vmq_passwd` and `vmq_acl` are off, anonymous access is off, TLS 1.2+ on 8883 is the only published listener (plain 1883 stays on loopback inside the container).
- Telemetry processing must stay out of webhooks: the hooks only answer allow or deny.
- A denied publish closes that client's connection (EMQX does the same with `deny_action = disconnect`; Mosquitto drops the message and keeps the connection). Agents reconnect on their own.

Licence: the official `vernemq/vernemq` image starts only with `DOCKER_VERNEMQ_ACCEPT_EULA=yes`. Commercial use of the official builds needs a VerneMQ subscription. The alternative is to build the image yourself from the Apache-2.0 source and set `VERNEMQ_IMAGE`.

### VerneMQ cluster

The bundled Compose service is **one node**. Several replicas do not form a cluster by themselves, and MeteorCloud does not claim high availability for it. A real cluster needs, per the VerneMQ documentation:

1. A unique, stable node name per node (`DOCKER_VERNEMQ_NODENAME`, a StatefulSet with a headless Service on Kubernetes).
2. The same Erlang cookie on every node (`DOCKER_VERNEMQ_DISTRIBUTED_COOKIE`), kept secret.
3. Nodes joined explicitly (`DOCKER_VERNEMQ_DISCOVERY_NODE` or `vmq-admin cluster join`), or VerneMQ's Kubernetes discovery (`DOCKER_VERNEMQ_DISCOVERY_KUBERNETES=1`) with the RBAC it needs.
4. The inter-node ports (44053 and the Erlang distribution range) open between nodes only.
5. Persistent storage per node and a TCP load balancer on 8883 in front.
6. The same six webhooks and the same server certificate on every node.

Run that cluster yourself (the VerneMQ Helm chart is the usual way) and connect MeteorCloud to it as an external broker: `mqtt_broker: external` / `mqtt.broker=external`, `MQTT_PROVIDER=vernemq`, `MQTT_BROKER_HOST=<load balancer>`. Test node loss and netsplits before you rely on it.

## TLS

All bundled brokers use the same three files from `MQTT_CERTS_DIR` (`ca.crt`, `server.crt`, `server.key`, key owned by UID 1000). `make mqtt-certs` creates a development CA and server certificate. For production, issue the server certificate from your own CA with these names:

- the public hostname devices use (`MQTT_PUBLIC_HOST`);
- the internal name the data plane uses: `mqtt-broker` on Compose, `<release>-mqtt` (Mosquitto) or `<release>-emqx` (EMQX) on Kubernetes.

Devices verify the broker with the CA they received at registration and log in with their own username and password. Client certificates (mTLS) are not implemented. Only TLS 1.2 and 1.3 are accepted.

## Data plane and later processing

The data plane connects as the platform user and only speaks standard MQTT, so it is the same for every broker. Rules for processing messages (alerts, forwarding, storage) belong in the data plane as consumers of MQTT 5 shared subscriptions (`$share/<group>/...`), which Mosquitto, EMQX, and VerneMQ all support. They do not belong in the broker or in auth callbacks. Shared subscriptions spread load across workers but are not durable storage: a consumer must tolerate duplicates (QoS 1) and lost messages while it is down.

Two rule sets are kept apart on purpose:

- **Security policy** (who may publish or subscribe): `app/mqtt/policy.py` in the control plane. Rules are data, so new rule sets are added there, not in broker config.
- **Processing rules** (what to do with a message): the data plane.

## Switching broker on an existing install

Devices keep their credentials and reconnect by themselves. Retained messages and open sessions on the old broker are lost.

- **Compose:** replace the broker profile in `COMPOSE_PROFILES` and set `MQTT_PROVIDER`, then `docker compose up -d --remove-orphans`. An existing `.env` with `MQTT_BROKER_HOST=emqx` keeps working: every bundled broker also answers to that name.
- **Ansible:** set `mqtt_provider` and re-run the playbook. Installs that say nothing get Mosquitto; set `mqtt_provider: emqx` to stay on EMQX.
- **Helm:** `mqtt.provider`. Moving from EMQX to Mosquitto renames the broker Service from `<release>-emqx` to `<release>-mqtt`: re-issue the server certificate with the new name, and expect a new load balancer address. Set `mqtt.provider: emqx` to keep everything as it was.

## Adding another broker

1. Make the broker ask `POST /internal/mqtt/authenticate` and `/authorize` ([contract](../../contracts/mqtt-http.md)), directly or through a small translation router like `vernemq_router.py`. Do not copy users or ACLs into it.
2. Add an adapter class to `app/adapters/mqtt_brokers.py` and its name to `MQTT_PROVIDER` in `app/core/config.py` and the data plane.
3. Add a Compose service with the profile of the same name and the `mqtt-broker` network alias.
4. Run `MQTT_PROVIDER=<name> make test-mqtt`. The suite is the acceptance test: it must pass unchanged.
