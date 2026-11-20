# Device agent contract suite

Checks that a device agent speaks [contracts/device-agent-v1.md](../../contracts/device-agent-v1.md)
exactly: every HTTP request and answer, every MQTT message, the new headers and capabilities.
Today it runs the Python `meteorcli`; the Go agent gets its own adapter and must pass the same
recordings.

```bash
make agent-contract-test          # verify, then replay
```

## How it works

- **Fake control plane** (`harness/fake_cp.py`): a local HTTP server with scripted answers. Every
  request and answer is checked against `contracts/schemas/agent` while the scenario runs; so are
  `User-Agent: meteorcli/<version>` and `X-Meteor-Agent-Protocol: 1`.
- **Mosquitto** (`harness/broker.py`): `eclipse-mosquitto:2` in Docker with a throwaway TLS CA. An
  observer plays the server side: it watches `devices/<id>/#`, sends `ping` and `sync_desired`, and
  checks QoS, schemas and the retained status.
- **Simulated device** (`harness/device.py`): docker, Wi-Fi, WireGuard and reboot go here, never to
  the host. Scenarios assert on what it was asked to do.
- **Adapter** (`agents/python_agent.py`): drives the agent through `register`, `configure`, `test`,
  `request_token`, `claim` and `run(heartbeats, between)`. The Python agent runs in-process with its
  real HTTP and MQTT clients.
- **Recordings** (`recordings/<scenario>.json`): the normalized transcript of each scenario. Ids,
  secrets, timestamps and host readings are placeholders (`<uuid:1>`, `<device_token>`,
  `<inventory>`), so they double as readable examples of the contract.

## Modes

| `AGENT_CONTRACT_MODE` | The fake control plane answers from | Then |
| --- | --- | --- |
| `verify` (default) | the scenario script | the transcript must equal the recording |
| `replay` | the recording (placeholders made concrete; desired software re-signed) | the transcript must equal the recording |
| `record` | the scenario script | the recording is rewritten |

Re-record only when an exchange changed on purpose, and review the diff like code.

## Scenarios

| Scenario | Covers |
| --- | --- |
| `register` | `register`, then a re-registration that proves itself with the device token |
| `enroll` | `test` (key check, health), `request-token`, `claim`: pending, 429 with `Retry-After`, approved |
| `heartbeat_mqtt` | heartbeat, MQTT config refresh, TLS connect, online/offline status (retained), metrics, `app.started` and `log` events, `ping` answered, `sync_desired` ignored |
| `actions` | reboot reported before it runs, set password, add user, an unknown kind reported `failed: Unsupported` |
| `wifi` | apply with `connect_now` confirmed against `/api/v1/health`, remove, report without passwords |
| `wireguard` | key registration, tunnel up, SSH CA trusted and reported at once |
| `docker_deploy` | signed docker release, key pinning, download through a storage redirect without the token, progress and `in_sync` reports |

Not covered yet: `update_agent` (starts the real updater), `docker_compose` and `systemd`
deploys, application credentials. They keep their unit tests in `src/device-plane/agent/tests`.

## Settings

| Variable | Meaning |
| --- | --- |
| `AGENT_CONTRACT_AGENT` | adapter to use (`python`) |
| `AGENT_CONTRACT_BROKER`, `AGENT_CONTRACT_BROKER_CA` | use your own TLS broker (`host:port`, CA file) instead of Docker |
| `CI` | when set, a missing broker fails the run instead of skipping the MQTT scenario |
