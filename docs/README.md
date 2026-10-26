# MeteorCloud documentation

Learn and operate MeteorCloud — from first boot to production. Website: [meteor-edge.com](https://meteor-edge.com).

## Start here

| Goal | Command | Doc |
| --- | --- | --- |
| Test or develop on your **laptop** | `make dev` | [Getting started](getting-started.md) |
| **Production** on a VM you already have | `make up` on that host | [Install quickstart](install-quickstart.md) |
| **Production**: create a new AWS EC2 | `make aws-up` from a laptop | [AWS deployment](aws-deployment.md) |
| **Production**: existing Kubernetes cluster | Helm | [Kubernetes](kubernetes.md) |
| Understand the three planes | — | [Concepts](concepts.md) · [Architecture](architecture.md) |
| Contribute or run an AI agent | — | [CONTRIBUTING.md](../CONTRIBUTING.md) · [AGENTS.md](../AGENTS.md) |
| Enroll a device with `meteorcli` | — | [Getting started → First device](getting-started.md#enroll-a-device) |

`make dev` is Compose on localhost (hot reload, seed user). `make up` is Ansible + production Compose on the server (Traefik, generated secrets, optional WireGuard). Do not mix them.

## Guides

How to use the product day to day.

| Topic | Doc |
| --- | --- |
| Users, organizations, members | [Identity and organizations](identity-and-organizations.md) |
| Roles and access | [Authorization (RBAC)](authorization/rbac.md) |
| Device types and groups | [Device types and groups](fleet/device-types.md) |
| Admin-initiated enroll | [Registration tokens](fleet/registration-tokens.md) · [Device registration](fleet/device-registration.md) |
| Device-initiated enroll | [Enrollment API keys](fleet/enrollment-api-keys.md) · [Request / approve / claim](fleet/device-request-enrollment.md) |
| Device HTTP auth & heartbeat | [Device authentication](fleet/device-authentication.md) · [Heartbeat](fleet/heartbeat.md) |
| MQTT connectivity | [MQTT](fleet/mqtt.md) |
| Artifacts (images, firmware, …) | [Artifacts](fleet/artifacts.md) |
| Releases (software versions) | [Releases](fleet/releases.md) |
| Deployments (send a release) | [Deployments](fleet/deployments.md) |
| Device VPN + browser SSH | [Connectivity](fleet/connectivity.md) |
| Public app URLs | [Endpoints](fleet/endpoints.md) |
| Operator console | [Console](console.md) |

## Reference

Commands and APIs you look up while working.

| Topic | Doc |
| --- | --- |
| Control-plane HTTP API | [API overview](api.md) · interactive OpenAPI at `/docs` on a running API |
| Device CLI | [meteorcli](meteorcli.md) |
| MQTT ↔ control-plane contract | [`contracts/mqtt-http.md`](../contracts/mqtt-http.md) |
| Architecture & providers | [Architecture](architecture.md) |

## Deploy and operate

| Path | Doc |
| --- | --- |
| Local testing / contributor setup (`make dev`) | [Getting started](getting-started.md) · [Development](development.md) |
| Production on a VM (`make up`) | [Install quickstart](install-quickstart.md) · [Deployment](deployment.md) |
| Kubernetes (existing cluster) | [Kubernetes](kubernetes.md) |
| DNS records to create (domain, apps, WireGuard) | [DNS records](dns.md) |
| `installation.yaml` | [Installer configuration](installer-configuration.md) |
| Modular services (`cloud_app`, `vpn`) | [Services](services.md) |
| Production: new AWS EC2 (`make aws-up`) | [Prerequisites](aws-prerequisites.md) · [AWS deployment](aws-deployment.md) |
| Upgrades · destroy · logs | [Upgrades](upgrades.md) · [Destroy](destroy.md) · [Observability](observability.md) |
| When something breaks | [Troubleshooting](troubleshooting.md) |
| EC2 smoke test (CI) | [AWS CI](aws-ci.md) |
| Full-stack E2E (one command, CI) | [E2E](e2e.md) |

## Related code docs

- Device agent package: [`src/device-plane/agent/README.md`](../src/device-plane/agent/README.md)
- Installer package: [`infrastructure/installer/README.md`](../infrastructure/installer/README.md)
