# Signed updates

Devices install only desired software that their organization signed. A stolen
database dump (without the encryption key), a tampered artifact bucket or a proxy
that rewrites responses can no longer make devices run something nobody deployed.
A Docker release that names its image by tag signs the tag, not the image content.
A registry that changes the image behind that tag can still change what devices run.
Pin the image by `@sha256:` digest to close this gap (see [Docker images](#docker-images)).

## How it works

- Each organization has an **Ed25519 signing key**, created the first time a device asks
  for its desired software. The private key is stored encrypted with
  `SECRETS_ENCRYPTION_KEY` (or a key derived from `APP_SECRET_KEY` when that is unset).
- Every item of `GET /api/v1/agent/software` carries `key_id`, `signature` and
  `artifact_sha256`. The signature covers the device id, workload, release id and
  version, revision, spec and the artifact's SHA-256, so an item cannot be moved to
  another device or pointed at a different file. The agent also refuses a revision
  older than the one it already applied for that workload.
- The agent (meteorcli 0.4.0 and later) **pins the organization's public keys** the
  first time it fetches `GET /api/v1/agent/signing-keys`, stored in
  `signing-keys.json` next to its config. From then on it refuses an item that is
  unsigned, signed by a key it does not trust, or whose signature does not verify, and
  it checks the downloaded artifact against the signed SHA-256. A refused item shows
  as **failed** on the device's software list with the reason.
- New device credentials (registration or enrollment) clear the pinned keys, so a device
  moved to another organization pins that organization's keys.

Pinning on first use trusts the server once, on the first signing-key fetch. Configure
the agent to reach the control plane over HTTPS so that first fetch cannot be
intercepted. `REGISTRATION_REQUIRE_HTTPS=true` only rejects plain-HTTP registration
and enrollment; it does not check the signing-key fetch.

## Rotating the key

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://fleet.example.com/api/v1/organizations/$ORG/signing-keys/rotate
```

Needs the `organization.update` permission and is recorded in the audit log as
`signing_key.rotated`. The old key signs the new one (`endorsed_by`, `endorsement`);
devices that trust the old key trust the new one on their next poll and keep the old
key for verification. `GET /api/v1/organizations/{org}/signing-keys` lists all keys.

## Losing the key

If `SECRETS_ENCRYPTION_KEY` / `APP_SECRET_KEY` changes, the server cannot read the
private key and the device software list fails. Keep these secrets with your backups
([Backups and restore](../backups.md)). If a key is lost for good, delete the
organization's rows from `organization_signing_keys` (a new key is created) and remove
`signing-keys.json` from each device's config directory (or register it again) so it
pins the new key.

## Upgrading

- Older agents ignore the new fields and keep working as before.
- A new agent talking to an older control plane (no `/agent/signing-keys`) accepts
  unsigned items until the server starts signing, then pins and enforces.

## Docker images

A signed docker item names its image by tag, which a registry could change. So docker
releases are **pinned to a digest** when they are saved: the server resolves the tag on
the registry (public HTTPS registries only, anonymous pull) and stores
`spec.image_digest`, which is part of the signed spec. The device runs
`image@sha256:…` and Docker checks the content against the digest.

- Private registry: give `image_digest` (or `image@sha256:…`) in the release; the
  server cannot log in to resolve it and otherwise keeps the tag.
- `RELEASE_REQUIRE_IMAGE_DIGESTS=true` refuses docker releases that cannot be pinned.
- `RELEASE_RESOLVE_IMAGE_DIGESTS=false` turns the registry lookup off (air-gapped installs).
- Images inside a docker_compose file are not pinned yet: write `image@sha256:…` in the
  compose file; the file itself is covered by the signed artifact hash.
