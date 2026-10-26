# Registration tokens

Tokens are one of two ways a new device joins the fleet. The other is a
[device-initiated enrollment request](device-request-enrollment.md).

## Security model

- Tokens are generated with `secrets.token_urlsafe` and carry a `reg_` prefix.
- Only a deterministic **SHA-256 hash** and a short non-secret **prefix** are
  stored. The plaintext is returned **once** at creation and never again.
- List responses never include the plaintext — only the prefix and metadata.
- Secret values are never written to logs.

## Options

When creating a token you may set:

- `name` — required label for identification.
- `device_type_id` / `device_group_id` — bind the token so every device that
  registers with it inherits the type/group. Both must belong to the same
  organization.
- `expires_at` — optional expiry (must be in the future).
- `max_uses` — optional cap on the number of successful registrations.

## Lifecycle

- `use_count` increments on each successful registration.
- A token is invalid once revoked, expired, or its `max_uses` is reached. All of
  these return `401 invalid_registration_token` at registration time.
- Revoke a token at any time; revocation is immediate and irreversible.

## API

Read access is available to any organization member. Create and revoke require
the **owner** or **admin** role.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/registration-tokens` | never includes plaintext |
| `POST` | `/api/v1/organizations/{org}/registration-tokens` | response includes one-time `token` |
| `POST` | `/api/v1/organizations/{org}/registration-tokens/{id}/revoke` | |

## UI

Create tokens under **Fleet → Devices → Add device**. After creation a dialog
shows the plaintext token **once**, with copy buttons and a ready-to-run
install + `meteorcli register` command (curl installer, then register against
the current site origin). The dialog recommends `--token-file` and clears the
secret from memory when closed.
