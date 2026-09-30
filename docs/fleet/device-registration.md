# Device registration

Devices join an organization by calling the agent registration endpoint with a
valid registration token.

## Endpoint

```
POST /api/v1/agent/register
```

Request body (all inventory fields optional except `token`):

```json
{
  "token": "reg_...",
  "name": "edge-01",
  "serial_number": "…",
  "mac_addresses": ["aa:bb:cc:dd:ee:ff"],
  "hostname": "edge-01",
  "os_name": "Ubuntu",
  "os_version": "22.04",
  "kernel_version": "6.2.0",
  "architecture": "x86_64",
  "cpu_model": "…",
  "cpu_cores": 4,
  "memory_mb": 8000,
  "labels": {},
  "metadata": {}
}
```

Response:

```json
{
  "device_id": "…",
  "device_token": "dev_…",
  "organization_id": "…",
  "name": "edge-01",
  "heartbeat_interval_seconds": 60,
  "mqtt": {
    "host": "localhost",
    "port": 8883,
    "username": "device_<uuid>",
    "password": "mqtt_…",
    "tls": true
  }
}
```

The `device_token` is the device's long-lived HTTP credential — store it securely.
The MQTT password is returned only once and is stored separately from the HTTP token.

## Atomic behavior

Registration is a single transaction: the token is validated, the tenant/type/
group derived, the device upserted, its credential rotated, and the token use
count incremented — committed once, or rolled back entirely on any failure.

## Duplicate detection and re-registration

Device identity is matched using hardware serial and overlapping
normalized MAC addresses:

- **Same organization, single match** → the existing device is updated
  (inventory refreshed) and its credential is rotated. The device ID is stable.
- **Match in another organization** → rejected with `409 device_registered_elsewhere`.
- **Multiple conflicting matches** → rejected with `409 ambiguous_device_identity`.

MAC addresses are normalized (lowercased, colon-separated); all-zero and
malformed values are ignored.

## Rate limiting

Registration is rate-limited per source IP using a fixed-window counter (default
10 requests / 60s), backed by Redis. Exceeding the limit returns
`429 rate_limited`. The limiter fails open if Redis is unreachable.

## Transport security

HTTP registration is currently **allowed** but the server logs a warning when it
is not HTTPS. Set `REGISTRATION_REQUIRE_HTTPS=true` to reject plain-HTTP
registration. Always use HTTPS in production.

## Using the reference agent

```bash
printf '%s' "reg_..." > /tmp/meteorcli.token
meteorcli register \
  --server https://platform.example.com \
  --token-file /tmp/meteorcli.token \
  --name edge-01
```

Devices can also [request enrollment](device-request-enrollment.md) without a
token, using an organization API key and administrator approval.

See [device-plane/agent/README.md](../../device-plane/agent/README.md).
