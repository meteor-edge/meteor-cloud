# Device-initiated enrollment

A device can request to join an organization. The request sits in **pending**
until an administrator approves or rejects it. After approval the device polls
and receives its `dev_` credential **once**.

This is the second enrollment path. The first path (a registration token) is
documented in [device-registration.md](device-registration.md).

## Flow

1. An administrator creates an [API key](enrollment-api-keys.md) under **Fleet → API keys**.
2. The device is configured (`meteorcli config --domain … --api-key …`) and runs
   `meteorcli request-token`.
3. `POST /api/v1/agent/enroll/request` (Bearer `key_…`) creates or **reuses** a
   pending request for that hardware and returns a one-time `claim_secret`
   (`clm_…`). The agent sends a **primary MAC** (default-route NIC, else
   Ethernet, else Wi-Fi) plus optional serial. Submitting again with the same
   serial or primary MAC updates that row and **rotates** the claim secret.
4. The administrator reviews the request on **Fleet → API keys** or **Fleet →
   Devices**. Pending rows show the **enrollment key name** and the **primary
   MAC**. Approve or reject it.
5. The device polls `POST /api/v1/agent/enroll/poll` with `request_id` and
   `claim_secret` for a limited time (`meteorcli request-token` defaults to 5 minutes,
   at most once every 10 seconds). On first poll after approval the server
   issues a `dev_` credential, creates/updates the device (same identity
   matching as token registration), and returns the token once.
6. If approval happens later, `meteorcli claim` polls with the claim secret
   stored on the device. A resubmit rotates that secret, so the latest
   `request-token` response (or rewritten claim file) is what `claim` uses.

## Device endpoints

```
GET /api/v1/agent/enroll/check
Authorization: Bearer key_...
```

Returns the organization and key label if the key is valid. Used by
`meteorcli test`. Does not create an enrollment request.

```
POST /api/v1/agent/enroll/request
Authorization: Bearer key_...
```

Request body is inventory (all fields optional). The server stores at most one
MAC (the first normalized address). If an unclaimed request in this organization
already matches the serial or that primary MAC (`pending` or `approved` and not
yet claimed), that row is reused: inventory is refreshed, the API key on the
row is updated, and a new `claim_secret` is issued. Requests with no serial and
no MAC are not deduped.

Response:

```json
{
  "request_id": "…",
  "claim_secret": "clm_…",
  "status": "pending",
  "poll_interval_seconds": 10,
  "expires_at": "…"
}
```

```
POST /api/v1/agent/enroll/poll
```

```json
{
  "request_id": "…",
  "claim_secret": "clm_…"
}
```

Statuses: `pending`, `approved`, `rejected`, `expired`. `device_token` is
present only on the first successful claim after approval.

## Admin endpoints

| Method | Path |
|--------|------|
| `GET` | `/api/v1/organizations/{org}/enrollment-requests?status=pending` |
| `POST` | `/api/v1/organizations/{org}/enrollment-requests/{id}/approve` |
| `POST` | `/api/v1/organizations/{org}/enrollment-requests/{id}/reject` |

Admin list/approve/reject responses include `enrollment_key_id`,
`enrollment_key_name`, and `enrollment_key_prefix` (null if the key was
deleted). `mac_addresses` is the primary MAC only for new requests.

Approve body (all optional): `{ "name", "device_type_id", "device_group_id" }`.
Reject body (optional): `{ "reason" }`. Mutation requires **owner** or **admin**.

## Rate limiting and security

- Submit: fixed-window limiter keyed by source IP + API key (default 5 / 60s).
- Poll: fixed-window limiter keyed by source IP + `request_id` (default 30 / 60s).
- Claim secrets are hashed (SHA-256); plaintext is never listed.
- A wrong `claim_secret` returns `401 invalid_enrollment_request`.
- Duplicate identity matching matches token registration (same org upsert,
  cross-org `409 device_registered_elsewhere`).
- Pending requests expire after `ENROLLMENT_REQUEST_TTL_SECONDS` (default 3600).
- HTTP is allowed but warned; `REGISTRATION_REQUIRE_HTTPS=true` rejects it.

If a secret leaks:

| Secret | Attacker can | Attacker cannot | Mitigate |
| --- | --- | --- | --- |
| `key_` | Create or refresh pending requests (rate-limited); see org name via check | Join the fleet without approve; call org APIs; obtain `dev_` | Revoke the key; never auto-approve |
| `clm_` + `request_id` | Poll; steal `dev_` on the first poll after approve | Approve; use other requests | Approve only expected hardware; reject; rotate the device credential if stolen |
| `dev_` | Act as **that one device** (heartbeat, MQTT for its id, its desired software, its WireGuard) | Other devices; org admin HTTP | Rotate or revoke the device credential |
| `reg_` | Register up to `max_uses` immediately | Admin APIs | `max_uses: 1`, revoke, expiry |

## Using meteorcli

```bash
meteorcli config --domain meteorxx.com --api-key key_...
meteorcli test
meteorcli request-token --name edge-01
# if still pending, later:
meteorcli claim
meteorcli run
```
