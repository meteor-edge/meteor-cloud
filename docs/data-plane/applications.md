# Applications and credentials

An **application** is the identity your code runs as. It is separate from the device identity: a device may run several applications, and an application may run on many devices or in the cloud.

| Term | Meaning |
| --- | --- |
| Application | Named identity inside one organization (`slug`, for example `meter`), with permissions |
| Permissions | Which streams the application may publish, subscribe to and read, and which commands it may send or receive |
| Credential | A token that lets one instance act as the application. Scoped to a device or to the cloud, with an environment label and an expiry |

## Quick start

All routes are under `/api/v1/organizations/{organization_id}` and need a signed-in user.

```bash
API=http://localhost:8000/api/v1
TOKEN=$(curl -s $API/auth/login -H 'content-type: application/json' \
  -d '{"email":"owner@example.com","password":"dev-password-123"}' | jq -r .access_token)
ORG=$(curl -s $API/organizations -H "authorization: Bearer $TOKEN" | jq -r '.[0].id')

# 1. Application with least-privilege permissions
APP=$(curl -s $API/organizations/$ORG/applications -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{
    "slug": "meter", "name": "Meter",
    "permissions": {"publish": ["energy.**"], "read": ["energy.**"], "subscribe": ["energy.**"],
                    "receive_commands": true}}' | jq -r .id)

# 2. A credential for a cloud-hosted instance. The token is shown once.
curl -s $API/organizations/$ORG/applications/$APP/credentials -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"name": "laptop", "environment": "dev", "expires_in_days": 30}' \
  | jq -r .token > my-app.token
```

## Permissions

```json
{
  "publish":   ["energy.**", "alerts.*"],
  "subscribe": ["energy.**"],
  "read":      ["energy.**"],
  "receive_commands": true,
  "send_commands": ["meter"]
}
```

`*` matches one name segment and a trailing `**` matches one or more. A bare `**` is refused, so no application can be granted everything by accident. `send_commands` lists application slugs of the same organization. An application with empty permissions can do nothing. Changing permissions (`PATCH .../applications/{id}`) takes effect within seconds, without new tokens.

## API

| Route | Does | Permission |
| --- | --- | --- |
| `GET /applications`, `GET /applications/{id}` | List, read | `application.read` |
| `POST /applications` | Create (`slug`, `name`, `description?`, `permissions?`) | `application.create` |
| `PATCH /applications/{id}` | Change name, permissions, `enabled` | `application.update` |
| `DELETE /applications/{id}` | Delete with all credentials | `application.delete` |
| `GET /applications/{id}/credentials` | List credentials (never the token) | `application.credential` |
| `POST /applications/{id}/credentials` | Create (`name`, `device_id?`, `environment?`, `expires_in_days?`). Returns the token once | `application.credential` |
| `POST /applications/{id}/credentials/{cid}/rotate` | New token for the same scope; the old one stops working at once | `application.credential` |
| `POST /applications/{id}/credentials/{cid}/revoke` | Stop the token | `application.credential` |
| `POST /applications/{id}/commands` | Send a command as an operator (`name`, `payload?`, `device_id?`) | `application.command` |
| `GET /applications/{id}/commands/{command_id}` | Command state and result | `application.command` |

Roles: viewers read applications; operators also send commands; developers, admins and owners manage applications and credentials and send commands. See [RBAC](../authorization/rbac.md).

## Credentials

- A token looks like `mca_...`. MeteorCloud stores only its SHA-256 hash. It cannot be shown again: create or rotate to get a new one.
- A credential belongs to one application and is either **device-scoped** (`device_id` set: every message it publishes is attributed to that device) or **cloud-scoped**.
- `environment` is a label carried on every message (`production`, `staging`, ...).
- Every credential expires. Console-created ones default to 90 days (1 to 365). Tokens the agent obtains for deployed workloads live `APP_CREDENTIAL_TTL_HOURS` (default 168) and are replaced automatically.
- Revoking a credential, disabling or deleting its application, or disabling its device cuts access within one policy poll (about 5 seconds), including open live connections.

## Applications deployed to devices

Bind a Docker or Docker Compose release to an application by adding `meteorcloud` to its spec:

```json
{"name": "meter", "version": "1.0.0", "kind": "docker",
 "spec": {"container_name": "meter", "image": "registry.example.com/acme/meter:1.0.0",
          "meteorcloud": {"application": "meter", "environment": "production"}}}
```

When the release is deployed, the agent on each device:

1. asks the control plane for a token for that workload. The control plane issues one only if the device's desired release really is bound to the application, so a device cannot obtain tokens for anything else. The token is scoped to that device;
2. stores it under `/etc/edge-agent/app-credentials/<workload>/token`, in a directory only root can enter;
3. starts the container with the file mounted read-only at `/run/secrets/meteorcloud-token` and with `METEORCLOUD_ENDPOINT`, `METEORCLOUD_TOKEN_FILE`, `METEORCLOUD_APP_ID`, `METEORCLOUD_DEVICE_ID`, `METEORCLOUD_ENVIRONMENT` and `METEORCLOUD_ORGANIZATION_ID` set. For Compose, an override file adds this to every service and the project's own compose file is not modified;
4. replaces the token in place before it expires. The container is not restarted and the SDK picks up the new token. The previous token stays valid for 10 minutes so nothing is interrupted.

The token is never placed in the container environment, the image, the compose file, the release spec or a log.

`METEORCLOUD_ENDPOINT` comes from `DATA_PLANE_PUBLIC_URL` on the control plane. A release may set its own in `env`. If neither exists the workload is reported as failed with that reason. Systemd releases cannot be bound.

This needs an agent release that includes the feature (after `agent-v0.2.0`).

## Audit log

Every change is recorded in the organization's audit log (`GET /audit-events`) by the acting user, with no secrets in the metadata:

| Action | When |
| --- | --- |
| `application.create`, `application.update`, `application.delete` | Application created, changed (including permissions and enable/disable), deleted |
| `application.credential.create`, `.rotate`, `.revoke` | Credential lifecycle by a user |
| `application.credential.mint` | A device agent obtained a token for a deployed workload |
| `application.command.send` | An operator sent a command through the control plane |

Commands sent from one application to another do not pass the control plane. They are kept, with issuer, target, state, result and timestamps, in the data plane's `commands` table and counted in its metrics.
