# Operator console and public website

The **console** is the operator UI in this repository (`console/`). The **website**
is a separate Next.js app in the private repo
[`meteor-edge/meteor-ui`](https://github.com/meteor-edge/meteor-ui). They do not
share a Vite/Next project.

The console never imports website code. It only opens docs as an ordinary URL
(`VITE_DOCS_BASE_URL`, default `http://localhost:3000/docs`).

## Local development

Console (this repo):

```bash
make install-console
make dev-console    # :5173  VITE_DOCS_BASE_URL → website docs
```

Website (private; do not copy it into this tree):

```bash
git clone git@github.com:meteor-edge/meteor-ui.git
cd meteor-ui
make install-website
make dev-website    # :3000
```

## Production images

Build from this repository:

```bash
docker build -f infrastructure/docker/Dockerfile.console \
  --build-arg VITE_API_BASE_URL=https://api.example.com \
  --build-arg VITE_DOCS_BASE_URL=https://www.example.com/docs \
  -t ghcr.io/meteor-edge/meteorcloud-console:0.1.0 .
```

AWS git-source deploy builds `Dockerfile.console` from this repository.
The website is not part of `cloud_app`; deploy it from `meteor-ui` separately
(Cloud Run, JSON content on S3). See `website/README.md` in that repo.
