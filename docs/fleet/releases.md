# Releases

A **release** is a version of software you can deploy to devices later: a name,
version, kind (`systemd`, `docker`, or `docker_compose`), optional artifact, and
kind-specific install spec.

Releases are organization-scoped. They reference [artifacts](artifacts.md) by ID
when the kind needs a file (systemd unit, compose bundle, or docker tarball).
A docker release may omit an artifact and pull `spec.image` on the device instead.

Each device tracks **desired** vs **installed** software (`GET
.../devices/{id}/software`). Desired state is set when you [Deploy](deployments.md);
agents report installed state over heartbeat. See [Heartbeat](heartbeat.md).

## API

Reading requires `artifact.read`. Creating and updating require `artifact.create`
/ `artifact.update`. Deleting requires `artifact.delete`.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/releases` | Filters: `kind`, `version`, `search`, `artifact_id`, `page`, `page_size`. Each item includes `artifact` (`id`, `name`, `version`, `type`, `file_name`) or `null` |
| `POST` | `/api/v1/organizations/{org}/releases` | JSON: `name`, `version`, `kind`, optional `artifact_id`, `description`, `spec` |
| `GET` | `/api/v1/organizations/{org}/releases/{id}` | |
| `PATCH` | `/api/v1/organizations/{org}/releases/{id}` | `description` and/or `spec` |
| `DELETE` | `/api/v1/organizations/{org}/releases/{id}` | |

### Spec by kind

| Kind | Artifact | Spec |
|------|----------|------|
| `systemd` | type `systemd` (required) | `unit_name` (required), `start` (default true) |
| `docker` | type `docker` tarball **or** none if pulling | `container_name`, and either `image` or an artifact |
| `docker_compose` | type `docker_compose` (required) | `project_name`, optional `env` |

Uniqueness is per organization on kind + name + version (case-insensitive).

## UI

**Releases** in the organization menu lists releases and lets you create one by
choosing a **deployment type**:

- **systemd service** — a Linux unit
- **Single container** — one container via `docker run`
- **Compose application** — an app run with Docker Compose (one or more services)

Then fill the matching artifact and install fields. The same flow can grow new
kinds later (for example AI models) without a separate wizard.

Releases and artifacts link both ways in the console:

- The **Releases** list has an **Artifact** column that opens that artifact
  (image-only docker releases show the image instead).
- The **Artifacts** list shows **Used by** (`release_count` in the API) linking
  to the releases filtered by that artifact, and a **Create release** action for
  systemd, docker and compose artifacts that opens the create dialog with the
  kind and artifact preselected.
- An artifact used by a release cannot be deleted (`409 artifact_in_use`); the
  Delete button is disabled until those releases are deleted.

## Apply notes

- **systemd** — agent installs the unit under `/etc/systemd/system`, then
  `daemon-reload` / `enable` / `restart` when `start` is true. Unprivileged
  `meteorcli run` uses the `meteorcli-systemd` helper, which the installer adds
  only when it runs as root (passwordless sudo for that file only):
  `curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | sudo bash`
  (from a cloud checkout: `sudo ./installcli.sh` in `src/device-plane/agent`).
  `meteorcli.service` already runs as root. Re-run the installer with `sudo`
  after upgrading if Deploy fails with permission denied on the unit file.
- **docker** — pull `spec.image` and/or `docker load` a tarball, then
  `docker run`.
- **docker_compose** — materialize YAML/zip and `docker compose up -d`.

## Deployment provider

Releases always live in MeteorCloud. Who **executes** a Deploy is selected with
`DEPLOYMENT_PROVIDER` (`native`, `none`, or `mender` stub). See
[Deployments](deployments.md).
