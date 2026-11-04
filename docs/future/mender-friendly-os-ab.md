# Future: Mender-friendly OS A/B

**Status:** planned — not implemented. Index: [Future plans](../future-plans.md).

Today MeteorCloud **native** Deploys update **apps** (systemd, Docker, Compose)
in place via `meteorcli`. Disk **A/B rootfs** OTA (automatic boot-partition
rollback) is out of scope for native. This note describes how to add that path
with **Mender** without replacing native app Deploys.

Related today: [Deployments → Provider](../fleet/deployments.md#provider)
(`mender` is a stub that returns 501), port `DeploymentProvider` in
`app/ports/deployment.py`, stub adapter `app/adapters/mender_deployment.py`.

## Goal

| Workload | Engine |
| --- | --- |
| Compose / systemd / docker apps | **native** (current) |
| OS / rootfs A/B | **Mender** (future) |

Keep stock “Raspberry Pi OS + install meteorcli” fleets on native only.
Mender-friendly means a **special image SKU**, not a toggle on a single-root SD card.

## Why not global `DEPLOYMENT_PROVIDER=mender`

`DEPLOYMENT_PROVIDER` is currently install-wide. Setting it to `mender` would
send **all** Deploys to Mender. Apps and OS must use **different** engines:
route by **release kind** (for example `rootfs` / `os` → Mender; `systemd` /
`docker` / `docker_compose` → native).

## Recommended model

```text
Golden Raspberry Pi OS image
        │
        ▼
  mender-convert  →  dual rootfs + data + U-Boot + mender-client
        │              (+ meteorcli in rootfs / overlay)
        ▼
  Flash field devices
        │
        ├─ App Deploys  → NativeDeploymentProvider → meteorcli
        └─ OS Deploys   → MenderDeploymentProvider → mender-client A/B
```

**Licensing default:** **bring-your-own Mender** (customer self-hosts, or you
manage their Mender). Do **not** bundle a Mender server in default Compose/Helm
until [Licensing](../licensing.md) records **Production OK** and maintainers
agree. Prefer permissive defaults for installs we ship to other companies.

## Device / image (Raspberry Pi)

Stock Pi OS from Raspberry Pi Imager (one boot + one root) cannot do Mender A/B
until reflashing.

1. Build a **golden** Raspberry Pi OS image (packages, `meteorcli`, connectivity
   readiness).
2. Run [mender-convert](https://docs.mender.io/operating-system-updates-debian-family/convert-a-mender-debian-image)
   with the matching board config (for example Pi 4/5, bookworm/trixie 64-bit).
3. Point `mender-client` at the customer’s Mender server URL / credentials.
4. Decide how **meteorcli** and device identity survive an OS swap (data
   partition, overlay, or re-bootstrap) and document that in the image recipe.
5. Flash that image to devices (or document the conversion for operators).

## Control plane

| Piece | Approach |
| --- | --- |
| Release kinds | Add `rootfs` (or `os`) for Mender Artifacts; keep app kinds on native |
| Provider routing | Per kind (or per Deploy): OS → Mender adapter; apps → native |
| Adapter | Implement create / cancel / status sync; store Mender id as `provider_ref` |
| Device link | Persist `mender_device_id` (or equivalent) on the MeteorCloud device |
| Artifacts | `.mender` Artifact in object storage and/or uploaded to Mender; Releases catalog stays in MeteorCloud |
| Rollout | Map phased cohorts to Mender deployments/groups where possible |
| Config / secrets | `MENDER_SERVER_URL` and tokens via env/Secret — adapters only, not domain services |

## Console and docs (when implementing)

- Same Deployments UI; OS releases clearly labeled.
- Device detail: show **OS** (Mender) and **apps** (native) separately.
- Operator guide: “App updates (native)” vs “OS A/B (Mender-friendly image required)”.
- Update [Deployments](../fleet/deployments.md) and [Licensing](../licensing.md)
  when the inventory or operator workflow changes.

## Non-goals (first version)

- Converting a live stock Pi to A/B without reflash.
- Using Mender Update Modules as the default path for Compose/systemd apps.
- Bundling Mender server in the default install before licensing review.

## Delivery phases

1. Image recipe / docs: golden → mender-convert → flash; meteorcli on A/B image.
2. Device identity: report/link Mender device id to the control plane.
3. Real `MenderDeploymentProvider` + `rootfs`/`os` kind + kind-based routing.
4. Console status sync + licensing inventory.
5. Optional later: managed Mender offering.

## References

- [Mender: convert a Debian/Raspberry Pi image](https://docs.mender.io/operating-system-updates-debian-family/convert-a-mender-debian-image)
- [Mender rollbacks / A/B update flow](https://docs.mender.io/overview/customize-the-update-process)
- Current stub: `src/control-plane/app/adapters/mender_deployment.py`
- Current port: `src/control-plane/app/ports/deployment.py`
