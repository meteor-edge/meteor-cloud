# Docker and Compose deploys

Same [Deploy](deployments.md) flow as [systemd](systemd.md). The agent applies
these kinds when the device reports `capabilities.docker` /
`capabilities.docker_compose` (typical on Raspberry Pi OS after installing
Docker Engine).

## Single container (`docker`)

1. Optional: upload a `docker` artifact (`docker save` tarball) for air-gap.
2. Create a release with kind `docker`, `spec.container_name`, and either
   `spec.image` (pull) or a tarball artifact (`docker load`).
3. Deploy. The agent runs `docker rm -f` then `docker run -d --name …`.

Optional spec: `ports`, `env`, `restart`, `command`.

## Compose (`docker_compose`)

1. Upload a `docker_compose` artifact (YAML file or zip containing
   `docker-compose.yml` / `compose.yaml`).
2. Create a release with kind `docker_compose` and `spec.project_name`
   (optional `spec.env`).
3. Deploy. The agent writes the project under
   `/var/lib/meteorcli/compose/<project>` and runs
   `docker compose -p … up -d`.

MeteorCloud does **not** host an OCI registry. Images must be pullable from the
device network, or loaded from a tarball.

## Related

- [Releases](releases.md)
- [Raspberry Pi OS](raspberry-pi.md)
- [Deployments](deployments.md)
