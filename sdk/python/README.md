# MeteorCloud SDK for Python

Publish data, subscribe to live data, query history and exchange commands from your application. The same code runs on a device that MeteorCloud deploys to and on a server in the cloud. You never deal with brokers, databases or topics.

```python
from meteorcloud import MeteorClient

client = MeteorClient.from_env()

client.telemetry.publish("energy.metrics", {"power": 1200, "voltage": 230})
```

## Install

Python 3.10 or newer.

```bash
pip install "meteorcloud @ git+https://github.com/meteor-edge/meteor-cloud#subdirectory=sdk/python"
```

From a checkout: `pip install ./sdk/python`. The package depends on `httpx` and `websockets` only.

## Configuration

`MeteorClient.from_env()` reads these variables.

| Variable | Meaning |
| --- | --- |
| `METEORCLOUD_ENDPOINT` | Address of the MeteorCloud data API, for example `https://cloud.example.com` |
| `METEORCLOUD_TOKEN_FILE` | File that holds the application token. Default `/run/secrets/meteorcloud-token` |
| `METEORCLOUD_TOKEN` | The token itself. Prefer the file |
| `METEORCLOUD_APP_ID`, `METEORCLOUD_DEVICE_ID`, `METEORCLOUD_ENVIRONMENT` | Informational. Who you are is decided by the token, never by these |
| `METEORCLOUD_CA_FILE` | CA bundle when the endpoint uses a private certificate authority |
| `METEORCLOUD_ALLOW_INSECURE` | `true` accepts an `http://` endpoint on a public address. Without it the SDK refuses, because the token would travel unencrypted. `http://` to this machine, a private network address or a single-word host name is always accepted |
| `METEORCLOUD_TIMEOUT` | Seconds for one request. Default 10 |
| `METEORCLOUD_RETRY_TIME` | Seconds a call keeps retrying before it raises. Default 30 |

**On a device.** When a release is bound to an application (see [Deploy with Docker](#deploy-with-docker)), MeteorCloud sets all of this for you: it mounts the token at `/run/secrets/meteorcloud-token` and sets the endpoint. Your code calls `from_env()` and nothing else.

**In the cloud or on your laptop.** Create a credential for the application in MeteorCloud, save the token to a file, and set two variables:

```bash
export METEORCLOUD_ENDPOINT=https://cloud.example.com
export METEORCLOUD_TOKEN_FILE=./my-app.token
```

You can also configure in code: `MeteorClient(endpoint="https://...", token_file="./my-app.token")`.

Never put a token in an image, a compose file or source control.

## Streams

Data goes to a named stream such as `energy.metrics`. A stream name is lowercase words joined by dots. Your application may publish, subscribe and read only the streams its permissions allow. Permissions use patterns: `*` matches one word and a trailing `**` matches one or more, so `energy.*` covers `energy.metrics` and `energy.**` also covers `energy.phases.l1`.

There are three kinds of data. They share one API and are kept apart:

| | Use for |
| --- | --- |
| `client.telemetry` | Measurements over time |
| `client.events` | Things that happened, such as an alert |
| `client.inference` | Results from a model |

## Publish

```python
result = client.telemetry.publish("energy.metrics", {"power": 1200, "voltage": 230})
client.events.publish("alerts.overheat", {"temperature": 91}, type="overheat")
client.inference.publish("vision.detections", {"label": "person", "confidence": 0.93})
```

`publish` returns when the message is durably stored. It raises `PublishError` if MeteorCloud rejects the message (wrong stream, no permission, bad data) and `UnavailableError` if it could not be stored within the retry time. A message is never dropped without you knowing.

Options:

- `time=` when the reading was taken (a `datetime` or RFC 3339 string). Default is now. Late and out of order data is fine.
- `seq=` your own sequence number, stored with the message.
- `id=` your own message id. Publishing the same id again stores nothing twice, so a publish is safe to repeat after a crash. Without it the SDK generates one and reuses it for its own retries.

Many at once:

```python
from meteorcloud import Message

results = client.telemetry.publish_many("energy.metrics", [
    {"power": 1200},
    Message({"power": 1210}, time="2026-11-11T10:00:05Z"),   # with its own time, id, seq or type
])
failed = [r for r in results if not r.ok]
```

## Subscribe

```python
with client.telemetry.subscribe("energy.*") as live:
    for event in live:
        print(event.device_id, event.stream, event.data)
```

Or with a callback, which is the usual shape for a service:

```python
@client.telemetry.on("energy.*")
def on_reading(event):
    print(event.data["power"])

client.run()   # blocks until SIGTERM or Ctrl+C, then shuts down cleanly
```

`subscribe(stream, device_id=..., application=...)` narrows a subscription to one device or one publishing application. Every event tells you who sent it: `event.application`, `event.device_id`, `event.environment`, `event.time`, `event.id`.

The live connection reconnects by itself and restores your subscriptions. Events published while you were disconnected are not replayed live. They are in history, so read them with `query` if you need every one.

## Query history

```python
from datetime import datetime, timedelta, timezone

since = datetime.now(timezone.utc) - timedelta(hours=1)

page = client.telemetry.query("energy.metrics", start=since, limit=100)
for event in page:
    print(event.time, event.data)

# next page
page = client.telemetry.query("energy.metrics", start=since, limit=100, cursor=page.next_cursor)

# or simply everything that matches
for event in client.telemetry.query_all("energy.metrics", start=since):
    ...
```

Filters: `start`, `end`, `device_id`, `application`, `order="desc"` for newest first, and `where={"severity": "warning"}` to keep messages whose data contains those values.

Aggregates over time buckets:

```python
buckets = client.telemetry.aggregate("energy.metrics", "power", function="avg", bucket="5m", start=since)
for b in buckets:
    print(b.start, b.value, b.count)
```

`function` is `avg`, `min`, `max`, `sum` or `count`. `bucket` is a width such as `30s`, `5m`, `1h`, `1d`. Nested fields work: `"phases.l1"`. Without `start` the window is the last 24 hours.

## Commands

One application can ask another to do something and get the result back. MeteorCloud delivers the command, retries until the target confirms it, and tracks its state: `PENDING`, `DISPATCHED`, `RECEIVED`, `EXECUTING`, then `SUCCEEDED`, `FAILED` or `TIMEOUT`.

Handle commands:

```python
@client.commands.handler("set-interval")
def set_interval(command):
    apply(command.payload["seconds"])
    return {"interval": command.payload["seconds"]}   # becomes the result

client.run()
```

Raising an exception marks the command `FAILED` with the error text. A command can arrive more than once, after a reconnect for example, so make handlers safe to repeat. A command sent while your application was offline is delivered when it connects, until the command expires.

Send commands:

```python
result = client.commands.call("meter", "set-interval", {"seconds": 1}, device_id=device_id, timeout=30)
```

`call` waits and returns the result, or raises `CommandError`. For more control:

```python
command = client.commands.send("meter", "reboot", device_id=device_id, expires_in=3600)
command = client.commands.wait(command.id, timeout=120)
print(command.state, command.result, command.error)
```

Leave out `device_id` to address the application wherever it runs. The sender needs the `send_commands` permission for the target application and the target needs `receive_commands`.

## Asyncio

`AsyncMeteorClient` has the same methods with `await`.

```python
import asyncio
from meteorcloud import AsyncMeteorClient

async def main():
    async with AsyncMeteorClient.from_env() as client:
        await client.telemetry.publish("energy.metrics", {"power": 1200})

        async with await client.telemetry.subscribe("energy.*") as live:
            async for event in live:
                print(event.data)

asyncio.run(main())
```

Callbacks and command handlers may be plain functions or coroutines in both clients.

## Errors

All errors derive from `MeteorCloudError` and carry a machine readable `code`.

| Error | When |
| --- | --- |
| `ConfigurationError` | Endpoint or token is missing or unreadable |
| `AuthenticationError` | Token invalid, expired or revoked |
| `PermissionDeniedError` | The application has no permission for that stream or command |
| `PublishError` | A message was rejected. `code` says why, for example `forbidden_stream`, `invalid_stream`, `payload_too_large` |
| `ValidationError` | The request is invalid and repeating it will not help |
| `RateLimitedError`, `UnavailableError` | Still failing after the retry time |
| `CommandError`, `CommandTimeoutError` | A command failed, timed out, or took longer than you waited |

## What the SDK does for you

- **Retries.** Network errors, overload and rate limits are retried with backoff for `METEORCLOUD_RETRY_TIME` seconds. Retries are safe because every message carries an id and every command an idempotency key.
- **Token rotation.** The token file is read again whenever it changes. MeteorCloud replaces the token before it expires and your application keeps running.
- **Reconnects.** The live connection comes back by itself and subscriptions are restored.
- **Shutdown.** `client.run()` handles SIGTERM and SIGINT. `client.close()` lets running command handlers finish first. Both clients work as context managers.
- **Logging.** Standard `logging`, logger name `meteorcloud`. Tokens and message contents are never logged.

Limits to know: a message's data is a JSON object of at most 64 KB, one publish call carries up to 500 messages per request (larger lists are split for you), and one page of history holds at most 1000 events.

## Deploy with Docker

Your image contains your code and the SDK. No token, no endpoint.

```dockerfile
FROM python:3.13-slim
WORKDIR /app
# git is needed only to install the SDK from the repository and is removed again
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir "meteorcloud @ git+https://github.com/meteor-edge/meteor-cloud#subdirectory=sdk/python" \
    && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY app.py .
USER 1000
CMD ["python", "app.py"]
```

In MeteorCloud:

1. Create the application and give it permissions, for example publish `energy.**` and receive commands.
2. Create a Docker release and bind it to the application by adding `meteorcloud` to the spec:

   ```json
   {
     "name": "meter", "version": "1.0.0", "kind": "docker",
     "spec": {
       "container_name": "meter",
       "image": "registry.example.com/acme/meter:1.0.0",
       "meteorcloud": {"application": "meter", "environment": "production"}
     }
   }
   ```

3. Deploy the release to devices.

On each device the agent obtains a token that is valid for that application on that device only, mounts it read-only at `/run/secrets/meteorcloud-token`, sets `METEORCLOUD_ENDPOINT` and starts your container. `docker_compose` releases work the same way and every service of the project gets the mount.

A complete example is in [examples/edge-app](examples/edge-app). [examples/cloud_dashboard.py](examples/cloud_dashboard.py) is the cloud side: it reads history, watches live data and sends a command.

## Develop without a MeteorCloud installation

The data API runs as a single binary with in-memory storage and a policy file:

From the repository root:

```bash
(cd src/data-plane/go && go build -o /tmp/dataplane ./cmd/dataplane)

TOKEN=mca_local_dev
cat > /tmp/policy.json <<EOF
{"applications": [{"id": "app-1", "organization_id": "org-1", "slug": "my-app",
   "permissions": {"publish": ["demo.**"], "subscribe": ["demo.**"], "read": ["demo.**"],
                   "receive_commands": true, "send_commands": ["my-app"]}}],
 "credentials": [{"id": "cred-1", "organization_id": "org-1", "application_id": "app-1", "device_id": null,
   "environment": "dev", "token_hash": "$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)",
   "expires_at": "2099-01-01T00:00:00Z"}]}
EOF

DATAPLANE_STORAGE=memory DATAPLANE_BUS=memory DATAPLANE_POLICY_FILE=/tmp/policy.json /tmp/dataplane &

METEORCLOUD_ENDPOINT=http://localhost:8082 METEORCLOUD_TOKEN=$TOKEN python sdk/python/examples/quickstart.py
```

With a full local stack (`make dev`), create the application and credential in MeteorCloud instead.

## Tests

```bash
cd sdk/python
pip install -e ".[dev]"
python -m pytest -q        # starts the real data API binary; needs Go, or METEORCLOUD_DATAPLANE_BIN
```

## Other languages

The SDK is a thin client over a documented protocol: [contracts/data-plane-v1.md](../../contracts/data-plane-v1.md). A client in another language needs HTTP and WebSocket and nothing else.
