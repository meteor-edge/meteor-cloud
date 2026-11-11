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
3. `POST /api/v1/agent/enroll/request` (Bearer `key_…`) creates a pending request
   for that hardware, or **reuses** an unclaimed one, and returns a one-time
   `claim_secret` (`clm_…`). The agent sends a **primary MAC** (Ethernet, else
   Wi-Fi, chosen by interface name so it stays the same when routes change) plus
   optional serial. Submitting again with the same serial or primary MAC updates
   a pending row and **rotates** the claim secret. An approved, unclaimed row is
   reused only for the same key and hardware; it stays approved, so the device can
   claim it without another approval. See [Device endpoints](#device-endpoints).
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

Request body is inventory (all fields optional). A request stores at most one
MAC (the first normalized address). A row is reused when it is unclaimed and:

- it is `pending` and matches the serial or that primary MAC, or
- it is `approved`, was submitted with this API key, and both the serial and the
  primary MAC are the same.

Reuse refreshes the inventory, updates the API key on the row, and issues a new
`claim_secret`. Any other submission creates a new pending request, so a different
key or different hardware never inherits an approval, and the original device's
`claim_secret` keeps working. Requests with no serial and no MAC are not deduped.

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

**Device names.** List responses include `suggested_name` for unclaimed requests,
and the console pre-fills it when approving, so nobody has to invent a name. It
is the first of these that applies:

1. The device's current name, if this hardware is already enrolled.
2. `--name` from `meteorcli request-token`.
3. The hostname, unless it is an OS default (`raspberrypi`, `ubuntu`, …) or `localhost`.
4. The OS default or `device`, plus the last 4 characters of the primary MAC (or the
   serial, if there is no MAC): `raspberrypi-a1b2`, `device-a1b2`.

`-2`, `-3`… is added if another device in the organization has that name. The same
rules apply if the approval has no name and to token registration. A name typed
when approving is used as it is; repeated names are allowed, and the console warns
about them. Renaming a device later does not change its existing public addresses.
Reject body (optional): `{ "reason" }`. Mutation requires **owner** or **admin**.

## Rate limiting and security

- Submit: fixed-window limiter keyed by source IP + API key (default 5 / 60s).
- Poll: fixed-window limiter keyed by source IP + `request_id` (default 30 / 60s),
  plus a cap per source IP across all requests (`ENROLLMENT_POLL_IP_RATE_LIMIT_REQUESTS`,
  default 600 / 60s, or 100 devices behind one address polling every 10 seconds).
- Claim secrets are hashed (SHA-256); plaintext is never listed.
- A wrong `claim_secret` returns `401 invalid_enrollment_request`.
- Duplicate identity matching matches token registration (same org upsert,
  cross-org `409 device_registered_elsewhere`).
- Pending requests expire after `ENROLLMENT_REQUEST_TTL_SECONDS` (default 3600).
- HTTP is allowed but warned; `REGISTRATION_REQUIRE_HTTPS=true` rejects it.

If a secret leaks:

| Secret | Attacker can | Attacker cannot | Mitigate |
| --- | --- | --- | --- |
| `key_` | Create or refresh pending requests (rate-limited); see org name via check; obtain `dev_` for an approved, unclaimed request made with that key by submitting its serial and primary MAC | Join the fleet without any approval; call org APIs; take over a request made with another key | Revoke the key (a `clm_` already issued can still poll); never auto-approve; approved requests cannot be rejected, so revoke the device credential if an unexpected device claims one |
| `clm_` + `request_id` | Poll; steal `dev_` on the first poll after approve | Approve; use other requests | Approve only expected hardware; reject; rotate the device credential if stolen |
| `dev_` | Act as **that one device** (heartbeat, MQTT for its id, its desired software, its WireGuard) | Other devices; org admin HTTP | Rotate or revoke the device credential |
| `reg_` | Register up to `max_uses` immediately | Admin APIs | `max_uses: 1`, revoke, expiry |

## Using meteorcli

```bash
sudo meteorcli config --domain meteorxx.com --api-key key_...
sudo meteorcli test
sudo meteorcli request-token --name edge-01
```

With `meteorcli.service` running (the default for a root install), the service
polls the request and connects once it is approved. Without the service, run
`meteorcli claim` after the approval and then `meteorcli run`.

## First-boot image

Automatic on current Raspberry Pi OS (bootfs has `user-data`). Do **not**
edit `cmdline.txt`.

1. Copy `.meteorenv` onto **boot** / **bootfs** (`METEOR_DOMAIN`, `METEOR_API_KEY`).
2. Append `packages:` and `runcmd:` from
   [`cloud-init-user-data.yaml`](../../src/device-plane/agent/packaging/firstboot/cloud-init-user-data.yaml)
   to `user-data`.
3. Boot the Pi. Approve the pending device in the console.

Details: [`packaging/firstboot/`](../../src/device-plane/agent/packaging/firstboot/README.md).
