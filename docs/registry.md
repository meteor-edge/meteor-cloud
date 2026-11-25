# Container registry

An organization can push its container images to MeteorCloud with plain `docker push`. The
registry is [CNCF Distribution](https://distribution.github.io/distribution/) v3 (`registry:3.1.2`,
Apache 2.0). It stores images; the control plane decides who may push and pull.

It is **off by default**. You can push, pull and browse images, and deploy them to devices. More is
planned ([#143](https://github.com/meteor-edge/meteor-cloud/issues/143)).

## Push an image

1. Console → **Settings → Registry** → create a token. It is shown once.
2. Sign in. The password is the token; the user name can be anything:

```bash
docker login registry.example.com -u me
docker tag my-app registry.example.com/acme/my-app:1.0
docker push registry.example.com/acme/my-app:1.0
```

`acme` is the organization's **namespace**, shown on the same page. It starts as the organization's
slug and never changes, so renaming the organization does not move its images. A deleted
organization's namespace is never given to another one (a new organization with that slug gets
`acme-2`).

## Images page

Console → **Software → Images** lists the organization's images with tags, digest, platforms, size,
who pushed them, and the pull command. Owners and admins can delete a tag or a whole image. The same
is in the API under `/api/v1/organizations/{id}/registry/images`.

The list is a copy of what the registry holds. The registry tells the API about every push and
delete, and the API compares both at start and every hour (`REGISTRY_RECONCILE_INTERVAL_SECONDS`, 0
turns it off), or now with `python -m app.cli registry-reconcile`. Deleting removes the tag; the data
stays in storage, and the image can still be pulled by digest.

## Run an app

**Images → Run**, or **device → Software → Run an app**: choose an image and tag (or a public image),
ports (default `8080 → 8080`), environment, and **Public URL**. The sheet follows it from "Waiting for
the device" over "Pulling and starting" to "Healthy" or "Failed", then offers **Open URL**.

It creates a release (named after the app, version from the tag) and an immediate Deploy to that one
device, plus the public address. Needs `deployment.create`, and `endpoint.create` for the URL;
operators can run apps without being able to manage releases. A device has one app per name
(`app_exists`).

| Setting | Meaning |
| --- | --- |
| Secret environment values | Stored encrypted, fetched by the device when it starts the app, never returned by the API. On the device they are visible to root (`docker inspect`). One line per value. Other values can be read by everyone who can read releases |
| Volumes | `name:/path` or `name:/path:ro`. Docker volumes named `<app>-<name>`, kept when the app is updated or removed. Paths on the device cannot be mounted |
| Memory, CPU | Limits of the container |

An agent too old for the registry or for these settings is refused (`agent_update_needed`).

**device → Software → Apps** lists running apps. **Edit** changes settings and deploys again;
**Update** appears when the tag points to a newer image or a newer tag was pushed. Each makes a new
release. A failed update rolls back to the release before; an app with secrets needs the server for
that.

```bash
curl -X POST "$API/api/v1/organizations/$ORG/devices/$DEVICE/apps/run" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"image": "my-app:1.0", "ports": ["8080:8080"], "endpoint": {}}'
# change: POST …/apps/my-app/redeploy {"image": "my-app:2.0"}     list: GET …/apps/deployed
```

## Deploy to devices

A `docker` release uses the registry when its spec has `"registry": "meteorcloud"`. `image` is a name
in the organization (`my-app:1.0`) or the full name:

```json
{"container_name": "my-app", "image": "my-app:1.0", "registry": "meteorcloud", "ports": ["8080:8080"]}
```

Saving the release fixes the digest (`image_digest`); it fails when the image is not there. Moving the
tag later does not change the release. A Deploy skips devices whose CPU the image was not built for,
and fails with `image_platform_mismatch` when that is all of them.

Devices need no registry token. For a desired release the agent asks for a password that works for
30 minutes, only for that repository, only to pull, and only while the release stays desired on that
device. It exists on the device only while the image is pulled, in a private temporary Docker config.
An image that is already on the device is not pulled again. The device must reach the registry host, and
needs an agent from this version on; an older agent fails with Docker's "denied" or "unauthorized".

## Tokens

| | Personal | Robot |
| --- | --- | --- |
| For | A person's laptop | CI |
| Acts as | That member, with their **current** role | The organization |
| Who creates it | Members with `image.read` (`image.push` for push) | Owners and admins |
| Stops working | Revoked, expired, member removed or account disabled | Revoked or expired |
| Limit to a path | No | Optional: `ci` allows `ci` and `ci/*` |

A client that already has an access token keeps its access until that expires (5 minutes by
default, `REGISTRY_TOKEN_TTL_SECONDS`), also after a revoke or role change.

"Push" includes pull. Owners and admins see and can revoke every token of the organization; others
see their own. Only a SHA-256 hash is stored. "Log out everywhere" does not revoke registry tokens.

In CI:

```bash
echo "$REGISTRY_TOKEN" | docker login registry.example.com -u robot --password-stdin
```

Permissions: `image.read` (every role), `image.push` (owner, admin, developer), `image.delete`
(owner, admin). See [RBAC](authorization/rbac.md).

## Enable it

You need a host name for the registry, normally `registry.<domain>` ([DNS](dns.md)).

**Ansible / `make up`**: set `registry_enabled: true` (needs an `https://` `platform_public_url` and `platform_domain`; or
`registry_public_host` instead of the domain when your own proxy ends TLS), then run the install again.

**Compose**: add `registry` to `COMPOSE_PROFILES` and set:

| Variable | Example | Meaning |
| --- | --- | --- |
| `REGISTRY_PROVIDER` | `distribution` | `none` (default) turns the registry off |
| `REGISTRY_PUBLIC_HOST` | `registry.example.com` | Host people push to; also the token audience |
| `REGISTRY_TOKEN_REALM` | `https://example.com/api/v1/registry/token` | Public address of the token endpoint |
| `REGISTRY_STORAGE_BUCKET` | | S3 only. Empty: the artifact bucket |
| `REGISTRY_STORAGE_PREFIX` | `registry` | S3 only. Prefix in the bucket |
| `REGISTRY_INTERNAL_URL` | `http://registry:5000` | How the API reaches the registry (default fits Compose; Helm sets it) |
| `REGISTRY_EVENTS_URL` | `http://backend:8000/internal/registry/events` | Where the registry sends push notifications (default fits Compose; Helm sets it) |
| `REGISTRY_ORG_QUOTA_BYTES` | `0` | Image bytes one organization may store; 0 is unlimited (Helm `registry.orgQuotaBytes`, Ansible `registry_org_quota_bytes`) |
| `REGISTRY_TOKEN_TTL_SECONDS` | `300` | Lifetime of access tokens |
| `REGISTRY_TOKEN_RATE_LIMIT_REQUESTS` | `600` | Token requests per source IP per minute |

On a laptop use `REGISTRY_PUBLIC_HOST=localhost:5000` and
`REGISTRY_TOKEN_REALM=http://localhost:8000/api/v1/registry/token`; Docker accepts plain HTTP on
`localhost` only. Traefik gets a certificate for the registry host when `PLATFORM_DOMAIN` is set.

**Helm**:

```yaml
config: { publicUrl: https://fleet.example.com }
ingress: { enabled: true, className: nginx }
registry:
  enabled: true
  host: registry.example.com
  ingress:
    tls: [{ secretName: meteorcloud-registry-tls, hosts: [registry.example.com] }]
```

The chart lifts ingress-nginx's body limit for the registry host. With another controller, set its
own annotations for large uploads in `registry.ingress.annotations`.

## Storage

Images use the same storage choice as [artifacts](fleet/artifacts.md):

- `filesystem`: a volume of its own (`registry_data`, `<platform_root>/data/registry`, or the
  `<release>-registry` claim). One replica only.
- `s3`: any S3-compatible bucket. With AWS S3, or when `OBJECT_STORAGE_PUBLIC_ENDPOINT_URL` equals
  `OBJECT_STORAGE_ENDPOINT_URL`, layers are downloaded straight from the bucket; otherwise they
  pass through the registry. MinIO is for local tests only ([licensing](licensing.md)). Create a
  separate `REGISTRY_STORAGE_BUCKET` yourself; the registry does not create buckets.

**Restoring PostgreSQL to an older backup:** restore image storage from the same time, or empty it.
A namespace claimed after that backup is forgotten by the database, and a new organization could
claim it while the old images are still stored.

**Quota:** once an organization's tagged images reach `REGISTRY_ORG_QUOTA_BYTES`, pushes are refused
until images are deleted; pulls go on. It is checked when a push starts, so the last push can go over.
Layers shared by two images count for each, and untagged images (including those kept for releases) do
not count. Settings → Registry shows the usage.

**Garbage collection:** deleting an image or a tag frees no storage by itself. On Compose, run
`scripts/registry-gc.sh` from `deploy/compose` (`--dry-run` first); the registry is stopped meanwhile.
Images that a release uses are kept by hidden `meteorcloud-pin-…` tags, so rollbacks keep working after
a tag moved or was deleted; deleting the release lets the image go. On Kubernetes, run
`kubectl exec deploy/<release>-backend -c backend -- python -m app.cli registry-reconcile`, make sure
nobody pushes, and run `registry garbage-collect --delete-untagged /etc/distribution/config.yml` in the
registry pod, then restart it.

**Deleting an organization:** its images are untagged by the next comparison (at most an hour) and
freed by the next garbage collection. Its namespace is never given to another organization.

**Not covered:** `scripts/backup.sh` does not include filesystem images; back up the volume yourself.

## How authorization works

1. The client asks the registry and gets a `401` that names the token endpoint and the scope.
2. It calls `GET /api/v1/registry/token` with the registry token as the Basic password.
3. The control plane checks the token and the role, and returns a 5-minute ES256 JWT with only the
   allowed actions, and only inside the organization's namespace. The catalog scope is never granted.
4. The registry verifies the JWT with a public key set.

The signing key is created on first use and stored encrypted in PostgreSQL. `python -m app.cli
registry-config <dir>` writes `config.yml` and `jwks.json` for the registry. Compose runs it when the
API starts, Helm in an init container. Changing `APP_SECRET_KEY` or `SECRETS_ENCRYPTION_KEY` makes
the stored key unreadable; rotate it then, or whenever you want a new one:

```bash
docker compose exec backend python -m app.cli registry-config /data/registry-config --rotate-key
docker compose restart registry
# Helm: pods write their own configuration
kubectl exec deploy/<release>-backend -c backend -- python -m app.cli registry-config /tmp/registry --rotate-key
kubectl rollout restart deploy/<release>-registry
```

Between the two commands, pushes and pulls are refused. Registry tokens keep working.

Audit: `registry_token.create`, `registry_token.revoke`, `registry_token.use` (refused sign-in of a
known token) and `image.access` (refused push or delete). See [Audit log](audit-log.md).
