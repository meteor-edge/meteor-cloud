# Operator console and public website

The **console** and **website** are separate frontend apps in the private repo
[`meteor-edge/meteor-ui`](https://github.com/meteor-edge/meteor-ui). They do not
share a Vite/Next project. Each has its own Compose file and image.

This MeteorCloud tree keeps empty placeholders (`console/README.md`,
`website/README.md`) so the directories still appear. Application source is not
here.

The console never imports website code. It only opens docs as an ordinary URL
(`VITE_DOCS_BASE_URL`, default `http://localhost:3000/docs`).

## Local development

You need SSH (or HTTPS) access to the private UI repo.

```bash
make checkout-ui
make install-console install-website
make dev-console    # :5173  VITE_DOCS_BASE_URL → website docs
make dev-website    # :3000
```

`METEOR_UI_REMOTE` and `METEOR_UI_REF` override the clone URL and branch.

Copied files are gitignored. They will not show up on the public remote.

Work on the UIs in a clone of `meteor-ui` (or here after `checkout-ui`, then copy
changes back). Do not `git add` console or website source in MeteorCloud.

## Production images

Build in `meteor-ui` CI or locally, push to a **private** registry:

```bash
docker build -f infrastructure/docker/Dockerfile.console \
  --build-arg VITE_API_BASE_URL=https://api.example.com \
  --build-arg VITE_DOCS_BASE_URL=https://www.example.com/docs \
  -t ghcr.io/meteor-edge/meteorcloud-console:0.1.0 .

docker build -f infrastructure/docker/Dockerfile.website \
  --build-arg SITE_URL=https://www.example.com \
  -t ghcr.io/meteor-edge/meteorcloud-website:0.1.0 .
```

Public Compose/Ansible can pull those images (`image_source: registry`) without
this source tree.

## AWS git-source deploy

Ansible clones `ui_repository_url` (default `git@github.com:meteor-edge/meteor-ui.git`)
onto the host and overlays `console/` before `Dockerfile.console`. The EC2 instance
needs a deploy key (or HTTPS token URL) that can read that private repo.

Website is not part of `cloud_app`; deploy it from `meteor-ui` separately.
