# Upgrades

On a VM installed with `make up`:

```bash
cd meteor-cloud
git pull
make up
```

Inventory, secrets, and `PLATFORM_DOMAIN` (`~/.meteorcloud/install.env`) are reused.

On AWS, update enabled services without recreating EC2 (unless Terraform detects required infra changes):

## Command

```bash
edge-installer upgrade installation.yaml
# or after editing platform.version / git_ref / images in installation.yaml
```

## What happens

1. Validates config and loads existing state
2. Runs Ansible `upgrade.yml` for **enabled services**:
   - **cloud_app**: copy the Compose bundle, re-render `.env`, pull or build images, `docker compose up --wait` (only changed services restart; the API runs migrations on start)
   - **vpn**: re-apply WireGuard role when enabled
3. Health check (cloud_app)
4. Updates installer state (`platform_version`, `enabled_services`)

## Version changes

Update before upgrade:

```yaml
platform:
  version: "0.3.0"

deployment:
  git_ref: master          # or a tag/commit
  backend_image: edge-platform-backend:0.3.0
  frontend_image: edge-platform-frontend:0.3.0
```

Push Git changes first when using `image_source: git`.

### Notes for specific changes

- **Production secrets are checked at start-up.** With `APP_ENV=production` the API
  refuses to start when `JWT_SECRET_KEY`, `APP_SECRET_KEY`, or `MQTT_INTERNAL_TOKEN`
  is empty, a public default or example value, or shorter than 16 characters.
  `MQTT_INTERNAL_TOKEN` is checked even when MQTT is off, because the internal MQTT
  routes stay mounted. `MQTT_PLATFORM_PASSWORD` is checked when MQTT is on.
  `SECRETS_ENCRYPTION_KEY` and `DATA_PLANE_INTERNAL_TOKEN` are checked when set.
  If `EDGE_PLATFORM_MQTT_INTERNAL_TOKEN` is unset, Ansible generates one under
  `/opt/edge-platform/secrets/mqtt_internal_token` and reuses it. Helm generates one
  too. A hand-written env file or an `existingSecret` can still hit this.
  Fix: `openssl rand -hex 32`.
- **Re-registering known hardware needs proof** (agent 0.3.5): the device's current
  token, or **Allow re-registration** on the device page. See
  [Device registration](fleet/device-registration.md#re-registering-known-hardware).
- **Signed desired software** (agent 0.4.0): devices pin the organization's signing key
  on first use and then refuse unsigned or tampered updates. The key is encrypted with
  `SECRETS_ENCRYPTION_KEY` / `APP_SECRET_KEY`: do not change those after upgrading. See
  [Signed updates](fleet/signed-updates.md).

## Idempotency

Running `apply` again is safe — it will not duplicate EC2 instances or reset admin passwords.

## Kubernetes

`helm upgrade --install` with new image tags. The API's init container runs
migrations before the new pods serve traffic. See [Kubernetes](kubernetes.md).

The chart's image repositories default to the published release images
(`ghcr.io/meteor-edge/meteorcloud-*`, see [Release images](release-images.md)). If you
build and push your own images, keep setting `backend.image.repository` and the other
`*.image.repository` values in your values file.

## Upgrading hosts deployed before `deploy/compose/`

Older installs ran a single generated `/opt/edge-platform/docker-compose.yml`. The
first upgrade with the current playbooks stops that stack, removes the old generated
files, and starts the new bundle from `/opt/edge-platform/compose` on the same data
directories. Redis is no longer started by default (in-memory rate limits); set
`components.redis.enabled: true` to keep it.

## Limitations

- No rolling or zero-downtime upgrades
- Infrastructure changes (instance type, etc.) require Terraform apply, which may replace the instance

## Related

- [AWS deployment](aws-deployment.md)
- [Modular services](services.md)
