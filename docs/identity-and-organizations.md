# Identity and organizations

Authentication and multi-tenant organizations.

## Concepts

- **User** — authenticated account (email + password)
- **Organization** — tenant boundary
- **Membership** — user ↔ organization with a role

### Roles

| Role | Capabilities |
| --- | --- |
| `owner` | Full control, including delete and assigning owner/admin |
| `admin` | Update org; manage member/viewer memberships |
| `member` | View organization and members |
| `viewer` | View organization and members |

## Auth API

```http
POST /api/v1/auth/register
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/logout-all
```

Access tokens are HS256 JWTs (PyJWT) signed with `JWT_SECRET_KEY`, valid for
`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (30). Each carries the user's `token_version` as `ver`.
`POST /api/v1/auth/logout-all` raises that version, so every token issued to the user
so far, including the one used for the call, stops working (`401`). Deactivating a user
or removing a membership already takes effect on the next request, because both are
checked on every call.

Register:

```bash
curl -s http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","full_name":"Example Owner","password":"strong-password"}'
```

Login:

```bash
curl -s http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","password":"strong-password"}'
```

### Sign-up and sign-in protection

| Setting | Default | Effect |
| --- | --- | --- |
| `ALLOW_SIGNUP` | `true` | `false` turns off `POST /api/v1/auth/register` (`403 signup_disabled`). People then join only when an administrator adds them to an organization (with a temporary password). Turn it off once your first administrator exists. Helm: `config.allowSignup`; Ansible: `platform_allow_signup`. |
| `LOGIN_RATE_LIMIT_REQUESTS` | `10` | Sign-in attempts per email address per window |
| `LOGIN_IP_RATE_LIMIT_REQUESTS` | `60` | Sign-in attempts per source IP per window |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `300` | Window length |

Over the limit, `POST /api/v1/auth/login` answers `429 rate_limited` with `Retry-After`, even for the right password, until the window ends.
<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:b39e44a316bf998fba36325fa09f9e261151acb5ef5aa317b77cd092ba98971d -->

Counters live in Redis (`CACHE_PROVIDER=redis`, shared by all API replicas) or in process memory.
While Redis is unreachable, the login limits keep counting in each API process's memory, so guessing stays limited (per replica) and sign-in keeps working. Device registration limits allow requests while Redis is unreachable.

<!-- /coderabbit-ai-generated:v1 -->
`GET /api/v1/auth/config` (public) returns `{"signup_enabled": …}`; the console uses it to show or hide the sign-up form.

## Organizations API

```http
GET    /api/v1/organizations
POST   /api/v1/organizations
GET    /api/v1/organizations/{id}
PATCH  /api/v1/organizations/{id}
DELETE /api/v1/organizations/{id}
GET    /api/v1/organizations/{id}/members
POST   /api/v1/organizations/{id}/members
PATCH  /api/v1/organizations/{id}/members/{membership_id}
DELETE /api/v1/organizations/{id}/members/{membership_id}
POST   /api/v1/organizations/{id}/leave
```

Create organization (Bearer token required):

```bash
curl -s http://localhost:8000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Acme Energy","slug":"acme-energy","description":"Demo"}'
```

Add an existing user:

```bash
curl -s http://localhost:8000/api/v1/organizations/$ORG_ID/members \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email":"member@example.com","role":"member"}'
```

## Tenant isolation

Organization queries are scoped by membership. Users without membership receive
`404 organization_not_found` so organization existence is not leaked.

## Migrations and seed

```bash
make migrate
make seed
```

Seed users (password `dev-password-123`):

- `owner@example.com` (owner)
- `admin@example.com` (admin)
- `member@example.com` (member)
- `viewer@example.com` (viewer)

Organization: `Acme Energy` (`acme-energy`)

Seeding refuses to run when `APP_ENV=production`.

## UI routes

- `/login`, `/register`
- `/organizations`
- `/organizations/new`
- `/organizations/:id`
- `/organizations/:id/members`
- `/organizations/:id/settings`
