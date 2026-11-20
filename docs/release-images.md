# Release images

Every MeteorCloud release publishes the platform images to GitHub Container Registry,
built for `linux/amd64` and `linux/arm64`, signed with [cosign](https://docs.sigstore.dev/)
and shipped with an SPDX SBOM.

| Image | Built from |
| --- | --- |
| `ghcr.io/meteor-edge/meteorcloud-backend` | `src/control-plane` (API, migrations, worker) |
| `ghcr.io/meteor-edge/meteorcloud-console` | `console` |
| `ghcr.io/meteor-edge/meteorcloud-data-plane` | `src/data-plane` (previous Python MQTT gateway; fallback for one release) |
| `ghcr.io/meteor-edge/meteorcloud-data-api` | `src/data-plane/go` (application data API, MQTT forwarder, JetStream workers) |
| `ghcr.io/meteor-edge/meteorcloud-mosquitto` | `src/data-plane/broker/mosquitto` (broker + auth plugin) |

Tags: `0.2.0` (exact release), `0.2` (latest patch of the minor) and `latest`. Pin the exact
version, or better its digest, in production. The digests of a release are listed in
`images.txt` on its [GitHub release](https://github.com/meteor-edge/meteor-cloud/releases).

The Helm chart defaults to these images at the chart `appVersion`. Compose and Ansible
build from source by default; set `BACKEND_IMAGE` and friends (`deploy/compose/.env.example`)
or `image_source: registry` (`deploy/ansible/inventory/example/group_vars/platform.yml`)
to use the release images.

## Verify a signature

Images are signed keylessly by the `container images` workflow on a `v*` tag, so the
certificate names that workflow and tag. Install cosign 2.x, then:

```bash
cosign verify ghcr.io/meteor-edge/meteorcloud-backend:0.2.0 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/meteor-edge/meteor-cloud/\.github/workflows/images\.yml@refs/tags/v'
```

A signature from any other repository, workflow or branch fails this check. To enforce it
in a cluster, use a policy controller such as Sigstore policy-controller or Kyverno
`verifyImages` with the same issuer and identity.

## Get the SBOM

Each image carries its SPDX SBOM (generated with syft) as a signed attestation:

```bash
cosign verify-attestation ghcr.io/meteor-edge/meteorcloud-backend:0.2.0 --type spdxjson \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/meteor-edge/meteor-cloud/\.github/workflows/images\.yml@refs/tags/v' \
  | jq -r .payload | base64 -d | jq .predicate > backend.spdx.json
```

The same files (`meteorcloud-<image>.spdx.json`) are attached to the GitHub release.
BuildKit also pushes its own SBOM and SLSA provenance with each image
(`docker buildx imagetools inspect <image> --format '{{ json .Provenance }}'`).

## Cutting a release (maintainers)

1. Bump `appVersion` in `deploy/kubernetes/helm/meteorcloud/Chart.yaml` (and `version` if
   the chart changed) and merge.
2. `git tag v0.2.0 && git push origin v0.2.0`. The workflow refuses a tag that differs
   from `appVersion`.
3. The workflow builds, pushes, signs, attests, and creates the GitHub release with the
   SBOMs and `images.txt`.

New GHCR packages are private by default: after the first release, set each
`meteorcloud-*` package to public in the organization's package settings so clusters can
pull without credentials. To test the pipeline without a release, run the workflow by
hand; it publishes only `sha-<commit>` tags.

The agent (`meteorcli`) is released separately; see [meteorcli](meteorcli.md).
