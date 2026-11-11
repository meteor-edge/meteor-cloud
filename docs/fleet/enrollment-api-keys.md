# API keys

An API key lets a device *ask* to join an organization without a
registration token. Keys are organization-scoped and managed under **Fleet →
API keys**.

## Security model

- Keys are generated with `secrets.token_urlsafe` and carry a `key_` prefix.
- Only a deterministic **SHA-256 hash** and a short non-secret **prefix** are
  stored. The plaintext is returned **once** at creation.
- List responses never include the plaintext.
- Revoked or expired keys are rejected with `401 invalid_api_key`.
- Submitting a request is rate-limited per source IP and key (default 30 / 60s) and per
  source IP before the key is checked (default 120 / 60s). One key can have at most 100
  pending requests (`ENROLLMENT_MAX_PENDING_PER_KEY`).

## Options

When creating a key you may set:

- `name` — required label.
- `expires_at` — optional expiry (must be in the future).

Device type and group are chosen later, when an admin approves the enrollment
request.

## API

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/enrollment-keys` | never includes plaintext |
| `POST` | `/api/v1/organizations/{org}/enrollment-keys` | response includes one-time `api_key` |
| `POST` | `/api/v1/organizations/{org}/enrollment-keys/{id}/revoke` | immediate |

Create and revoke require **owner** or **admin**. Any member may list keys.

## Using meteorcli

```bash
meteorcli config --domain meteorxx.com --api-key key_...
meteorcli test
meteorcli request-token --name edge-01
meteorcli claim   # if approval happened after the first wait ended
```

See [device-request-enrollment.md](device-request-enrollment.md).
