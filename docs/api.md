# API overview

The control plane exposes a versioned HTTP API under `/api/v1`. Interactive docs (OpenAPI/Swagger) are at **`/docs`** on a running API (local: http://localhost:8000/docs).

Auth for operators: `Authorization: Bearer <jwt>` from `POST /api/v1/auth/login`.  
Auth for devices: `Authorization: Bearer <device_token>` (`dev_…`) or enrollment API key where noted.

Internal MQTT callbacks (`/internal/mqtt/*`) are **not** operator APIs; they require `X-MQTT-Internal-Token`.

## Health

| Method | Path |
| --- | --- |
| `GET` | `/health` |
| `GET` | `/api/v1/health` |

## Auth (operators)

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/v1/auth/register` | Create account |
| `POST` | `/api/v1/auth/login` | Returns JWT |
| `GET` | `/api/v1/auth/me` | Current user |

Examples: [Identity and organizations](identity-and-organizations.md).

## Organizations

Prefix: `/api/v1/organizations`

| Method | Path |
| --- | --- |
| `GET` / `POST` | `/` |
| `GET` / `PATCH` / `DELETE` | `/{organization_id}` |
| `GET` / `POST` | `/{organization_id}/members` |
| `PATCH` / `DELETE` | `/{organization_id}/members/{membership_id}` |
| `POST` | `/{organization_id}/leave` |
| `GET` / `POST` | `/{organization_id}/teams` |
| … | team members under `.../teams/{team_id}/members` |

## Authorization

| Method | Path |
| --- | --- |
| `GET` | `/api/v1/authz/catalog` |
| `GET` | `/api/v1/me/access` |
| `GET` / `POST` | membership access under org authz routes |

See [RBAC](authorization/rbac.md).

## Fleet (organization-scoped)

Prefix: `/api/v1/organizations/{organization_id}`

| Resource | Paths (typical) |
| --- | --- |
| Device types | `/device-types`, `/device-types/{type_id}` |
| Device groups | `/device-groups`, `/device-groups/{group_id}` |
| Registration tokens | `/registration-tokens` (+ revoke) |
| Enrollment keys | `/enrollment-keys` (+ revoke) |
| Enrollment requests | list / approve / reject under enrollment request routes |
| Devices | `/devices`, `/devices/{device_id}`, enable/disable, MQTT revoke |
| MQTT test | `/mqtt/events` (SSE), `/mqtt/publish` |
| Audit | `/audit-events` |
| Artifacts | `/artifacts` (separate router; upload, download, download-link) |

Fleet guides: [docs/fleet/](fleet/).

## Agent (device-facing)

Prefix: `/api/v1/agent`

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/register` | Registration token in body |
| `POST` | `/heartbeat` | Device token |
| `GET`/`POST` | `/enroll/check` | Enrollment API key |
| `POST` | `/enroll/request` | Enrollment API key |
| `POST` | `/enroll/poll` | Claim secret |

Guides: [Device registration](fleet/device-registration.md), [Device-initiated enrollment](fleet/device-request-enrollment.md), [Heartbeat](fleet/heartbeat.md).

## Example: login and list devices

```bash
TOKEN=$(curl -s http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","password":"dev-password-123"}' \
  | jq -r .access_token)

curl -s http://localhost:8000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN"

curl -s "http://localhost:8000/api/v1/organizations/$ORG_ID/devices" \
  -H "Authorization: Bearer $TOKEN"
```

For request/response bodies, use OpenAPI or the linked fleet guides — this page is the map, not a full schema dump.
