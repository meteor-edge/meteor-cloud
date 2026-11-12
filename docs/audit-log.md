# Audit log

Every security-relevant or state-changing action in an organization writes one row to
`audit_events` (PostgreSQL). Rows are append-only: nothing in the API edits or deletes them.

Owners and admins read them in the console (**People → Audit log**) or through the API:

```http
GET /api/v1/organizations/{organization_id}/audit-events
```

| Query | Meaning |
| --- | --- |
| `limit` | 1–500, default 100. Newest first. |
| `action` | Exact action (`device.delete`) or a prefix ending in `.` or `*` (`device.`, `deployment.*`). |
| `resource_type`, `resource_id` | e.g. `device` and a device id: the whole history of one device. |
| `actor_user_id`, `actor_type` | Who acted (`user`, `device`, `registration_token`, `enrollment_key`, `enrollment_request`, `anonymous`, `system`). |
| `outcome` | `success`, `denied`, `failure`, `partial`. |
| `since` | Only rows at or after this time. |
| `before`, `before_id` | Next page: `created_at` and `id` of the last row you already have. |

Each row has: `action`, `resource_type`, `resource_id`, `outcome`, `metadata` (JSON),
`created_at`, the actor (`actor_type`, `actor_id`, `actor_user_id`, `actor_email`), and the
request it came from (`request_id`, which matches the `X-Request-ID` header and the access log,
plus the client `ip` and `user_agent`).

The client IP is the address uvicorn reports. Behind Traefik or an ingress it is the real
client only when the proxy is listed in `FORWARDED_ALLOW_IPS` (the shipped Compose and Helm
setups do this).

## What is recorded

| Area | Actions |
| --- | --- |
| Sign-in | `auth.register`, `auth.login` (success, and `outcome=failure` with `reason`: `bad_password`, `inactive`, `unknown_email`) |
| Refused requests | `access.denied`: any 403 on an organization route, with method, route and error code |
| Organization | `organization.create`, `organization.update` (before/after of name, slug, description), `organization.delete` |
| Members and teams | `member.invite`, `member.role_change`, `member.scope_change`, `member.remove`, `member.leave`, `team.create`, `team.update`, `team.delete`, `team.member_add`, `team.member_remove` |
| Device catalog | `device_type.create/update/delete`, `device_group.create/update/delete` |
| Enrollment secrets | `registration_token.create/revoke`, `enrollment_key.create/revoke` |
| Enrollment | `enrollment_request.submit` (device), `enrollment_request.approve/reject` (operator), `enrollment_request.claim` (device receives its credential), `device.register`, `device.reregister` |
| Devices | `device.update` (before/after of name, type, group), `device.enable`, `device.disable`, `device.delete`, `device.credential.rotate`, `device.credential.revoke`, `device.mqtt_credential.issue`, `device.mqtt_credential.revoke`, `device.command.ping`, `mqtt.test_publish` |
| Remote access | `connectivity.enroll` (first or new WireGuard key), `connectivity.revoke`, `connectivity.reenroll`, `connectivity.key_change_rejected`, `connectivity.host_key_mismatch`, `device.shell.start/end/denied`, `device.shell.certificate` (`meteorcloud-ssh`), `device.logs.start/end/list/denied` |
| Software | `artifact.upload/update/delete`, `artifact.download_link.create`, `release.create/update/delete`, `deployment.create/cancel/pause/resume/retry_failed/rollback` |
| Public apps | `endpoint.create/update/delete` |

Not recorded on purpose: reads, heartbeats, MQTT authentication of every connection (see the
`MQTT_AUTH` metric instead), WireGuard reconnects with an unchanged key, and enrollment retries
that update an existing request.

Events about an unknown email or a deleted organization have no organization. They stay in
the table (`organization.delete` keeps the id in `metadata.organization_id`) but are not listed
by the organization API.

## Adding an audit event (for contributors)

Use the one recorder; do not write `audit_events` another way:

```python
from app.audit.service import AuditRecorder

AuditRecorder(session).record(
    actor=user,                       # or actor_type="device", actor_id=device.id
    organization_id=organization_id,
    action="thing.verb",              # <resource>.<verb>, snake_case
    resource_type="thing",
    resource_id=thing.id,
    metadata={"name": thing.name},    # never secrets: a prefix or fingerprint at most
)
session.commit()                      # same transaction as the change itself
```

The recorder fills request id, client IP, user agent and, when `actor` is omitted, whoever
authenticated the request (user, device or enrollment key). Pass `outcome="denied"` or
`"failure"` for refusals; that also stops the generic `access.denied` row for the same request.
Add the new action to the table above and a test.
