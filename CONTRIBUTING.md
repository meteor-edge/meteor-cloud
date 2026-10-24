# Contributing to MeteorCloud

This is **one git repository**. Operators install images or run `make up`. Contributors clone the tree. Devices install `meteorcli` from a GitHub Release, not a cloud checkout.

Coding agents: follow [AGENTS.md](AGENTS.md).

## Users (run the product)

You do not need to send a pull request.

**Cloud on a VM you already have**

```bash
git clone https://github.com/meteor-edge/meteor-cloud.git
cd meteor-cloud
make up PLATFORM_DOMAIN=fleet.example.com
```

Details: [docs/install-quickstart.md](docs/install-quickstart.md). Laptop testing is `make dev` ([docs/getting-started.md](docs/getting-started.md)), not `make up`.

**Device agent** (no cloud clone):

```bash
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | bash
```

See [docs/meteorcli.md](docs/meteorcli.md). Maintainers: `make agent-dist` then `git tag agent-v<version> && git push origin agent-v<version>`.

**Bugs and features:** [GitHub Issues](https://github.com/meteor-edge/meteor-cloud/issues).

## Contributors (change the code)

### Prerequisites

From [docs/development.md](docs/development.md):

- Docker and Docker Compose v2.24+
- Python 3.13+
- Node.js 22+
- Make
- Optional for local Kubernetes: k3d, kubectl, helm

### Setup

```bash
cp deploy/compose/.env.example deploy/compose/.env
python -m venv .venv
source .venv/bin/activate
make install
make dev
make seed      # owner@example.com / dev-password-123
```

Console: http://localhost:5173 · API: http://localhost:8000 · OpenAPI: http://localhost:8000/docs.

Stop with `make stop`.

### Tests and lint

Commands from the [Makefile](Makefile). Do not invent others.

```bash
make test                  # installer, control-plane, data-plane, agent, console
make backend-test
make data-plane-test
make agent-test
make agent-dist            # pack meteorcli for a GitHub Release
make console-test
make check-plane-imports   # planes must not import each other
make lint
```

Control-plane tests need PostgreSQL (`make dev` first) and use a sibling `*_test` database, never the app DB.

Deployment checks that CI also runs (when you touch those paths):

```bash
make compose-config
make helm-lint
make terraform-check
make ansible-check ansible-lint
```

### Pull requests

- One focused change. Do not mix refactors with a feature.
- Add or update tests for behavior changes.
- Update [docs/](docs/README.md) when install, CLI, APIs, contracts, or operator flows change.
- Do not commit secrets, `.env` files with real passwords, private keys, or tokens. `deploy/compose/.env` is local; `~/.meteorcloud/secrets.env` is generated on the host.
- Keep HTTP/MQTT compatibility unless the PR is an explicit breaking change.
- Planes talk over HTTP/MQTT only. Do not import `app` from the data plane or agent, or `data_plane` / `edge_agent` from the control plane.

Architecture and allowed dependencies: [docs/architecture.md](docs/architecture.md).
