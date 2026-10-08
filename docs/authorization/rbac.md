# MeteorCloud RBAC and authorization

Status: **Phases 1–3 implemented** (custom roles deferred)  
Audience: product + engineering  
Goal: an administrator opens any member and immediately sees *which organizations, which resources, and exactly what actions* that person can perform.

### Recommended decisions (locked)

| Question | Choice |
|----------|--------|
| Legacy `member` role | Maps to **Operator** |
| Ungrouped devices | Visible only to **organization-wide** memberships |
| Device types / artifacts | **Organization-wide** (not device-group scoped) |

## People model: Members and Teams

Admins see two concepts, never a separate “Users” list:

| Concept | Meaning | Storage |
|---------|---------|---------|
| **Member** | A person in the organization: email, name, role, device-group scope, access | `organization_memberships` (+ the account it signs in with) |
| **Team** | A named group of members in one organization | `teams`, `team_members` (keyed by membership id) |

- Adding a member is how a person is defined. If the email is new, the account is created in the same request (§8).
- The sign-in account (`users` table: email, password hash, active flag) is an implementation detail of the **identity directory** port, not a product concept. One account can be a member of several organizations.
- **Teams grant no permissions in v1.** Role and device-group scope stay on each member, so team changes never change what anyone can do. Removing a member from the organization removes them from its teams; deleting a team keeps its members.

### Identity directory port

Tenancy finds or creates the account behind a member through `app/ports/identity.py` (`IdentityDirectory`: `get_by_id`, `get_by_email`, `ensure_account`), injected into `OrganizationService` via FastAPI dependencies. The adapter is picked by `IDENTITY_PROVIDER`; today only `local` exists (`app/adapters/local_identity.py`, PostgreSQL `users` + bcrypt). An external IdP (OIDC/SCIM) would be a new adapter behind the same port. Login and JWT issuing still live in `app/identity/`.

## Implemented shape

| Piece | Now |
|-------|--------|
| Membership | `role_id` → system `roles`; optional `access_bindings` for device groups |
| Teams | `teams` + `team_members`; `team.*` permissions; Members show team badges |
| Checks | `AuthzService.require(membership, "resource.action", device_group_id=…)` |
| Scope | Zero bindings = entire org; group bindings limit device / device_group resources |
| Frontend | People → Members (scope, teams, Access page; add-member form sets scope) and People → Teams |
| Module | `app/authorization/` catalog, models, seed, service, router |

---

## 1. Authorization architecture

```text
User
  └── OrganizationMembership (one per org the user belongs to)
        ├── Role (system template or later custom)
        │     └── Permissions (resource.action)
        └── AccessBindings (optional scopes)
              └── scope_type = device_group | organization
                  scope_id   = group UUID | null (org-wide)
```

**Central module** (control plane):

```text
app/authorization/
  catalog.py          # permission + system-role definitions (code, versioned)
  models.py           # Role, RolePermission, AccessBinding (+ migrate membership)
  service.py          # AuthzService.check / require / effective_access / preview
  schemas.py          # API DTOs for access views
  dependencies.py     # FastAPI RequirePermission("device.reboot")
  router.py           # /me/access, member access, access-preview
```

Domain services call `AuthzService.require(...)` instead of `if role == admin`.

**Algorithm (always):**

1. Authenticate (existing JWT / device credential).
2. Resolve target `organization_id` from the route or resource.
3. Load active membership for `(user, organization)` → else **403** (or **404** when leaking existence would cross tenants — see §13).
4. Load role → permission set.
5. If the permission is not in the set → **deny**.
6. If the membership has **no** device-group bindings → treat as **organization-wide**.
7. If bindings exist → resource must fall under one of those device groups (see §10).
8. Default **deny**.

No explicit-deny rules in v1. Not granted = denied.

**Boundaries:**

- Control plane owns human RBAC (this doc).
- Device MQTT ACL stays on its own path (device credentials), but future agent ops that need human-equivalent rights go through the same permission catalog when applicable.
- Frontend may hide buttons from `/me/access`; server remains authoritative.

---

## 2. Permission catalog

Stable id: `{resource}.{action}`  
Stored as `resource` + `action` columns; id is derived.

| Id | UI label | Description |
|----|----------|-------------|
| `organization.read` | View organization | See org overview and settings that are not ownership-only. |
| `organization.update` | Update organization | Change name, description, and non-ownership settings. |
| `organization.delete` | Delete organization | Permanently delete the organization. |
| `member.read` | View members | List members and open Access views. |
| `member.invite` | Invite members | Add users to the organization. |
| `member.update` | Update members | Change a member’s role or scope. |
| `member.remove` | Remove members | Remove a member from the organization. |
| `team.read` | View teams | List teams and see who is on them. |
| `team.create` | Create teams | Create new teams. |
| `team.update` | Update teams | Rename teams and add or remove their members. |
| `team.delete` | Delete teams | Delete teams. Members stay in the organization. |
| `device_group.read` | View device groups | List and open device groups in scope. |
| `device_group.create` | Create device groups | Create device groups. |
| `device_group.update` | Update device groups | Edit device groups in scope. |
| `device_group.delete` | Delete device groups | Delete device groups in scope. |
| `device.read` | View devices | List and open devices in scope. |
| `device.create` | Create / enroll devices | Create tokens, approve enrollment, register devices. |
| `device.update` | Update devices | Edit device metadata, type, group, enable/disable. |
| `device.delete` | Delete devices | Remove devices. |
| `device.reboot` | Reboot devices | Send reboot (and similar operate) commands. |
| `device_type.read` | View device types | List and open device types. |
| `device_type.create` | Create device types | Create hardware models. |
| `device_type.update` | Update device types | Edit device types. |
| `device_type.delete` | Delete device types | Delete device types. |
| `artifact.read` | View artifacts | List and download artifacts. |
| `artifact.create` | Upload artifacts | Upload new artifact versions. |
| `artifact.update` | Update artifacts | Edit artifact metadata. |
| `artifact.delete` | Delete artifacts | Delete artifacts and their stored objects. |
| `deployment.read` | View deployments | List and open deployments. |
| `deployment.create` | Create deployments | Start deployments. |
| `deployment.cancel` | Cancel deployments | Cancel in-progress deployments. |
| `deployment.rollback` | Rollback deployments | Roll back a deployment. |
| `enrollment_key.read` | View enrollment API keys | List enrollment keys. |
| `enrollment_key.manage` | Manage enrollment API keys | Create and revoke enrollment keys. |
| `mqtt.test` | Use MQTT test tools | Publish/listen in the org MQTT test UI. |

**v1 rules:**

- Catalog lives in code (`catalog.py`), seeded into `permissions` on migrate/boot.
- No `"*"` / `"everything"` permission. Owner is a role that *includes* all permissions.
- Deployments permissions exist in the catalog before the Deployments product ships; endpoints return 404 until built, but Access UI can show them as “reserved”.

---

## 3. Role matrix (system roles)

System roles: `organization_id IS NULL`, `is_system = true`, immutable via API.

| Permission | Owner | Admin | Operator | Developer | Viewer |
|------------|:-----:|:-----:|:--------:|:---------:|:------:|
| organization.read | ✓ | ✓ | ✓ | ✓ | ✓ |
| organization.update | ✓ | ✓ | | | |
| organization.delete | ✓ | | | | |
| member.read | ✓ | ✓ | | | |
| member.invite | ✓ | ✓ | | | |
| member.update | ✓ | ✓ | | | |
| member.remove | ✓ | ✓ | | | |
| team.read | ✓ | ✓ | ✓ | ✓ | |
| team.create | ✓ | ✓ | | | |
| team.update | ✓ | ✓ | | | |
| team.delete | ✓ | ✓ | | | |
| device_group.read | ✓ | ✓ | ✓ | ✓ | ✓ |
| device_group.create | ✓ | ✓ | | | |
| device_group.update | ✓ | ✓ | | | |
| device_group.delete | ✓ | ✓ | | | |
| device.read | ✓ | ✓ | ✓ | ✓ | ✓ |
| device.create | ✓ | ✓ | ✓ | | |
| device.update | ✓ | ✓ | ✓ | | |
| device.delete | ✓ | ✓ | | | |
| device.reboot | ✓ | ✓ | ✓ | | |
| device_type.read | ✓ | ✓ | ✓ | ✓ | ✓ |
| device_type.create | ✓ | ✓ | | ✓ | |
| device_type.update | ✓ | ✓ | | ✓ | |
| device_type.delete | ✓ | ✓ | | ✓ | |
| artifact.read | ✓ | ✓ | ✓ | ✓ | ✓ |
| artifact.create | ✓ | ✓ | | ✓ | |
| artifact.update | ✓ | ✓ | | ✓ | |
| artifact.delete | ✓ | ✓ | | ✓ | |
| deployment.read | ✓ | ✓ | ✓ | ✓ | ✓ |
| deployment.create | ✓ | ✓ | ✓ | ✓ | |
| deployment.cancel | ✓ | ✓ | ✓ | ✓ | |
| deployment.rollback | ✓ | ✓ | | | |
| enrollment_key.read | ✓ | ✓ | | | |
| enrollment_key.manage | ✓ | ✓ | | | |
| mqtt.test | ✓ | ✓ | ✓ | ✓ | |

**Migration of today’s enum:**

| Old role | New system role |
|----------|-----------------|
| `owner` | Owner |
| `admin` | Admin |
| `member` | Operator (closest match; was “can use org but not manage”) |
| `viewer` | Viewer |

**Admin vs Owner:** Admin has full operational + member management, but **not** `organization.delete`. Transferring ownership stays an Owner-only flow (reuse today’s rules: only owners assign owner).

**Custom roles:** schema supports `organization_id NOT NULL` roles; **UI/API for creating them is phase 2**.

---

## 4. Resource / scope model

**Scope types (v1):**

| `scope_type` | Meaning |
|--------------|---------|
| `organization` | Entire org (or simply: **no binding rows** = org-wide) |
| `device_group` | One device group UUID |

**v1 product rule:** a membership is either:

- **Org-wide:** zero `device_group` bindings, or
- **Scoped:** one or more `device_group` bindings.

Mixing “org-wide + extra groups” is forbidden.

**What scope applies to:**

| Resource | Scope rule |
|----------|------------|
| Device | Device’s `device_group_id` must be in the binding set. Devices with `device_group_id IS NULL` are **only** visible to org-wide memberships. |
| Device group | Group id must be in the binding set. |
| Device type, artifact, deployment (org-level), members, organization, enrollment keys, mqtt test | **Not device-group scoped** in v1. If the role grants the permission, it applies org-wide. Document this clearly in the Access UI (“Organization-wide permissions” vs “Scoped to device groups”). |

**Why:** Device types and artifacts are org catalogs today, not per-group. Scoping them later is possible without changing the permission ids.

**Inheritance (only this):**

```text
Device Group (in scope)
  └── Devices in that group (in scope)
```

No other inheritance. No “parent group”, no folder trees.

---

## 5. PostgreSQL schema

```text
permissions
  id              UUID PK
  resource        VARCHAR(64) NOT NULL
  action          VARCHAR(64) NOT NULL
  label           VARCHAR(120) NOT NULL      -- "Reboot devices"
  description     TEXT NOT NULL
  UNIQUE (resource, action)

roles
  id              UUID PK
  organization_id UUID NULL FK organizations ON DELETE CASCADE
  key             VARCHAR(64) NOT NULL       -- owner|admin|operator|developer|viewer
  name            VARCHAR(120) NOT NULL
  description     TEXT NOT NULL
  is_system       BOOLEAN NOT NULL DEFAULT false
  UNIQUE (organization_id, key)  -- NULLS NOT DISTINCT for system keys

role_permissions
  role_id         UUID FK roles ON DELETE CASCADE
  permission_id   UUID FK permissions ON DELETE CASCADE
  PRIMARY KEY (role_id, permission_id)

-- Evolve memberships:
organization_memberships
  ... existing columns ...
  role_id         UUID NOT NULL FK roles     -- replaces enum column after backfill
  status          VARCHAR(32) NOT NULL DEFAULT 'active'  -- active|disabled
  -- drop enum column `role` after cutover

access_bindings
  id              UUID PK
  membership_id   UUID NOT NULL FK organization_memberships ON DELETE CASCADE
  scope_type      VARCHAR(32) NOT NULL       -- organization | device_group
  scope_id        UUID NULL                 -- device_groups.id when device_group
  created_at      TIMESTAMPTZ NOT NULL
  UNIQUE (membership_id, scope_type, scope_id)  -- NULLS NOT DISTINCT
  CHECK (
    (scope_type = 'organization' AND scope_id IS NULL) OR
    (scope_type = 'device_group' AND scope_id IS NOT NULL)
  )
```

Indexes: `(membership_id)`, `(scope_type, scope_id)`.

**Seed:** insert permissions + five system roles + role_permissions in the migration (or a deterministic seed step run from migration).

---

## 6. API design

### Effective access (self)

`GET /api/v1/me/access`

```json
{
  "organizations": [
    {
      "organization_id": "...",
      "organization_name": "Acme Energy",
      "role": { "key": "operator", "name": "Operator", "is_system": true },
      "scope": {
        "mode": "device_groups",
        "device_group_ids": ["..."],
        "device_group_names": ["Berlin"]
      },
      "permissions": ["device.read", "device.update", "device.reboot", "..."],
      "summary": {
        "granted_count": 18,
        "denied_count": 7,
        "by_resource": {
          "device": ["read", "update", "reboot"],
          "member": []
        }
      }
    }
  ]
}
```

### Member access (admin view)

`GET /api/v1/organizations/{org_id}/members/{membership_id}/access`

Same shape as one org entry above, plus:

```json
{
  "membership_id": "...",
  "user_id": "...",
  "email": "...",
  "full_name": "...",
  "permissions_detail": [
    {
      "id": "device.reboot",
      "label": "Reboot devices",
      "description": "...",
      "resource": "device",
      "action": "reboot",
      "granted": true,
      "source": "role",
      "role_key": "operator",
      "reason": null,
      "scope_mode": "device_groups"
    },
    {
      "id": "device.delete",
      "label": "Delete devices",
      "description": "...",
      "resource": "device",
      "action": "delete",
      "granted": false,
      "source": null,
      "role_key": null,
      "reason": "not_in_role",
      "scope_mode": null
    }
  ]
}
```

### Update role / scope

`PATCH /api/v1/organizations/{org_id}/members/{membership_id}`

```json
{
  "role_key": "operator",
  "scope": { "mode": "device_groups", "device_group_ids": ["...", "..."] }
}
```

Requires `member.update`. Role assignment rules: Owner can assign any system role; Admin cannot assign Owner (same spirit as today).

### Access preview

`POST /api/v1/organizations/{org_id}/access-preview`

```json
{
  "membership_id": "...",
  "permission": "device.reboot",
  "resource_type": "device",
  "resource_id": "..."
}
```

Response:

```json
{
  "allowed": false,
  "steps": [
    { "ok": true, "detail": "Alice is an active member of Acme Energy" },
    { "ok": true, "detail": "Role Operator includes device.reboot" },
    { "ok": false, "detail": "Device is in Munich; Alice is scoped to Berlin only" }
  ]
}
```

### Authz in handlers

```python
membership = authz.require_membership(user_id=user.id, organization_id=org_id)
authz.require(membership, "device.reboot", device_group_id=device.device_group_id)
```

`require_membership` raises `NotFoundError` (404) when the user has no active membership. For collection reads, `require_role_permission(membership, "device.read")` checks the role only; the caller applies the device-group scope in the query.

Raises `UnauthorizedError` (401) if no actor; `ForbiddenError` (403) if membership/permission/scope fails; uses `NotFoundError` (404) when the resource is outside the actor’s org (tenant isolation).

---

## 7. Frontend UX / navigation

**People → Members** (existing route, upgraded):

| Column | Content |
|--------|---------|
| Name | full name |
| Email | email |
| Role | Operator |
| Access scope | `Berlin, Munich` or `Entire organization` |
| Status | Active / Disabled |
| Last active | from `users.last_login_at` when available; else `—` |

Row click → **Member detail**.

**Member detail** tabs:

1. **Overview** — identity, status, joined at  
2. **Access** — summary + effective permissions (primary)  
3. **Activity** — audit events for this membership (phase 1.5 if audit filter is easy; else stub)

Sidebar: a **People** section holds **Members** (shown to roles with `member.read`) and **Teams** (shown to roles with `team.read`); viewers see neither. Members show their teams; Each team lists its members; owners/admins pick members to add from the organization or remove them from the team. No new top-level “RBAC” menu.

UI chrome for permissions: green check / muted strike; show label + monospace id on hover or secondary line.

---

## 8. Add-member flow

Replace single “email + role” form with a wizard:

1. **Identity** — email. If the person is new, also full name + temporary password; `IdentityDirectory.ensure_account` creates their sign-in account in the same transaction as the membership. People who already have an account are attached as-is (name/password ignored); disabled accounts are rejected.  
2. **Role** — Owner / Admin / Operator / Developer / Viewer (filtered by assignable rules)  
3. **Scope** — Entire organization **or** multi-select device groups  
4. **Review** — Access Summary preview (computed by `POST .../access-preview/role` or client using catalog + selected role matrix from API)

API: extend `POST .../members` body:

```json
{
  "email": "alice@example.com",
  "full_name": "Alice Doe",
  "password": "temporary-password",
  "role": "operator",
  "scope": { "device_group_ids": ["..."] }
}
```

`full_name` and `password` are required only when the email has no account (`422 member_account_details_required` otherwise). The password follows the registration rules and is never written to audit metadata; `member.invite` records `created_account: true|false`.

---

## 9. Effective-access view

Top of Access tab:

```text
Access Summary
  Role: Operator
  Organization: Acme Energy
  Device Groups: Berlin, Munich
  Permissions: 18 granted · 7 denied

Effective access
  • Devices: View, Update, Reboot
  • Device Groups: View
  • Artifacts: View
  • Deployments: View, Create, Cancel
  • Members: None
```

Then full grouped matrix (all permissions for that resource, granted and not).

Each granted row: `Source: Operator role` · `Scope: Berlin, Munich` (or Entire organization).  
Each denied row: `Reason: Not included in Operator role`.

No direct grants in v1 UI.

---

## 10. Access-preview design

Admin tool on Member Access (and optionally Settings → Access preview):

Inputs: member (prefilled), permission (select), resource type + id (optional).

Output: allowed/denied + ordered human steps (from `AuthzService.explain`).

Used for support debugging; not required for every CRUD path.

---

## 11. Audit-log design

Reuse `audit_events` with actions:

| action | metadata |
|--------|----------|
| `member.invite` | role_key, scope |
| `member.role_change` | old_role_key, new_role_key |
| `member.scope_change` | old_scope, new_scope |
| `member.remove` | role_key |
| `member.disable` / `member.enable` | status |
| `team.create` | name, membership_ids |
| `team.update` | name |
| `team.delete` | name |
| `team.member_add` / `team.member_remove` | membership_id |

Fields already on audit: actor, organization_id, resource_type=`membership` (or `team`), resource_id, metadata JSON. Do not log passwords or tokens.

---

## 12. Authorization test matrix

| # | Case | Expect |
|---|------|--------|
| 1 | No membership | 403/404 on org routes |
| 2 | Viewer + `device.update` | 403 |
| 3 | Operator + `device.reboot` in Berlin | 200 |
| 4 | Operator + `device.delete` | 403 |
| 5 | Operator scoped Berlin, device in Berlin | allow |
| 6 | Operator scoped Berlin, device in Munich | 404/403 |
| 7 | Operator scoped Berlin, device with null group | deny |
| 8 | Admin + `member.invite` | allow |
| 9 | Admin + assign Owner | 403 |
| 10 | Same user Owner in Org A, Viewer in Org B | isolation |
| 11 | Disabled membership | deny |
| 12 | Device moved Berlin → Munich | subsequent reboot denied |
| 13 | Cross-org resource id | 404 |
| 14 | `/me/access` lists only orgs user belongs to | |
| 15 | Effective permissions match role matrix | |
| 16 | Access preview explains scope miss | |

Unit-test `AuthzService` without HTTP; API tests cover enforcement on one representative route per permission family.

---

## Implementation phases

| Phase | Deliverable |
|-------|-------------|
| **0** | This doc + agreement on open questions |
| **1** | Schema, seed, `AuthzService`, migrate memberships, replace backend role checks |
| **2** | `/me/access`, member access API, access preview API, audit events |
| **3** | Members list scope column, member Access tab, invite wizard |
| **4** | Custom roles (optional), Activity tab, deployment endpoints when product lands |

---

## Open questions (need your call)

1. **Map old `member` → Operator?** (recommended) or keep a system role literally named `Member`?
2. **Unscoped devices** (`device_group_id` null): only org-wide roles see them (recommended), or also any scoped operator?
3. **Device types / artifacts:** org-wide for all roles that have the permission (recommended for v1), or defer UI that pretends they are scoped?
4. **Implement now through phase 1 only**, or through phase 3 (UI) in one push?
