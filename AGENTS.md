# Agent instructions (MeteorCloud)

These rules apply to **any** coding agent working in this repository (Cursor, Claude Code, Codex, Copilot, Aider, Continue, and others). Humans: see [CONTRIBUTING.md](CONTRIBUTING.md). Architecture: [docs/architecture.md](docs/architecture.md).

## Before you change code

- Read the existing code and contracts first. Do not invent APIs or services.
- Prefer a small, focused diff. Do not rewrite unrelated files.
- Keep the current architecture unless the task explicitly authorizes a change.
- Do not add dependencies, frameworks, services, or abstraction layers without a concrete need.
- Do not split this git repository, re-extract the console, or migrate planes to another language unless asked.

## Architecture (must follow)

- **Control plane** stays Python (`src/control-plane/`, package `app`).
- **Data plane** and **device plane** may use other languages later. Do not start that migration here.
- Planes must not import one another’s private Python packages. Talk over HTTP, MQTT, and documented contracts ([`contracts/mqtt-http.md`](contracts/mqtt-http.md)).
- PostgreSQL is the control-plane database. Vendor clients stay behind ports/adapters.
- Kubernetes, Terraform, Ansible, and WireGuard belong under `deploy/` / `infrastructure/`, not domain modules.
- VPN is an optional deploy service, not a reason to restructure the repo.
- Console talks only to the control-plane API. Production uses same-origin (`window.location.origin`).
- Device types are catalog rows, not separate codebases.
- The device agent source stays in this repo. Devices install from a GitHub Release (`install-meteorcli.sh`), not by cloning the cloud. Publish with tag `agent-v<pyproject-version>`.

## Directory notes

| Path | Rule |
| --- | --- |
| `console/` | Browser → control-plane HTTP only. Never call the data plane or import device code. |
| `src/control-plane/` | No vendor SDKs in domain services. Data plane is HTTP (`DATA_PLANE_URL`). |
| `src/data-plane/` | MQTT ingest; HTTP to the control plane only. Do not import `app`. |
| `src/device-plane/agent/` | `meteorcli` on the device. HTTP/MQTT to the API. Needs sibling `src/device-plane/runtime/` at install time. |
| `src/device-plane/gateway/` | Host process: poll `/internal/connectivity/*`, `wg set`. HTTP only. |
| `deploy/` | Images, Compose, Ansible, Helm, env. Not business logic. |

## Quality

- Never commit secrets, credentials, private keys, tokens, or real env files that contain them.
- Preserve existing HTTP/MQTT compatibility unless a breaking change is explicitly requested.
- Add or update tests for behavior changes.
- When install, config, contracts, or operator workflows change, update the matching page under `docs/` ([docs/README.md](docs/README.md)).
- Run the relevant tests (`make test`, or `make backend-test` / `data-plane-test` / `agent-test` / `console-test` / `make check-plane-imports`). Report exactly what ran and what did not.
- Do not claim tests, deploys, or installs succeeded unless they actually ran and passed.

## Further reading

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/development.md](docs/development.md)
- Cursor-only extras (ignore if you are not Cursor): `.cursor/rules/`
