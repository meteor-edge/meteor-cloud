---
title: HTTP API
description: Operator, agent, and internal MQTT endpoints.
section: Reference
order: 20
---

Base URL is the control plane (locally `http://localhost:8000`). Interactive schema: `/docs` (Swagger) and `/redoc`.

Operator routes use `Authorization: Bearer <access_token>` from `POST /api/v1/auth/login`. Organization-scoped fleet routes sit under `/api/v1/organizations/{organization_id}/`.

## Auth

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | Create user |
| POST | `/api/v1/auth/login` | `{ email, password }` → JWT |
| GET | `/api/v1/auth/me` | Current user |

## Organizations

| Method | Path |
| --- | --- |
| GET, POST | `/api/v1/organizations` |
| GET, PATCH, DELETE | `/api/v1/organizations/{id}` |
| GET, POST | `/api/v1/organizations/{id}/members` |
| PATCH, DELETE | `/api/v1/organizations/{id}/members/{membership_id}` |
| POST | `/api/v1/organizations/{id}/leave` |

## Fleet (organization-scoped)

Device types and groups: `/device-types`, `/device-groups`.

Registration tokens: `GET/POST .../registration-tokens`, revoke via POST on a token id.

Enrollment API keys: `GET/POST .../enrollment-keys`, revoke similarly.

Enrollment requests: list pending, approve, reject.

Devices: list (paginated), get, patch, delete, enable, disable, rotate/revoke HTTP credential, revoke MQTT, `POST .../devices/{id}/commands/ping`.

MQTT test UI: `GET .../mqtt/events` (SSE), `POST .../mqtt/publish`.

Audit: `GET .../audit-events`.

## Agent (device HTTP)

Prefix `/api/v1/agent`. Uses the enrollment API key or the device token as documented in the agent pages.

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/register` | Registration token from the console |
| POST | `/heartbeat` | Last-seen |
| GET/POST | `/enroll/check` | Poll enrollment |
| POST | `/enroll/request`, `/enroll/claim` | Device-initiated path |

## Internal MQTT (shared secret)

Header `X-MQTT-Internal-Token`. Not a user API. Called by EMQX or the data plane.

**Control plane**

- `POST /internal/mqtt/authenticate` `{ username, password }` → `{ result, is_superuser }`
- `POST /internal/mqtt/authorize` `{ username, action, topic }` → `{ result }`
- `POST /internal/mqtt/ingest` `{ topic, payload }` → `{ ok: true }`

**Data plane** (`DATA_PLANE_URL`, default port 8081)

- `GET /health`
- `POST /v1/publish` `{ topic, payload, qos, retain }`
- `POST /v1/subscriptions` and `/v1/subscriptions/unwatch` `{ topic }`

Health: `GET /health` and `GET /api/v1/health` on the control plane.
