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
```

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
