# MeteorCloud documentation

Learn and operate MeteorCloud — from first boot to production. Website: [meteor-edge.com](https://meteor-edge.com).

## Start here

| Step | Doc |
| --- | --- |
| 1. Run the stack locally | [Getting started](getting-started.md) |
| 2. Understand the three planes | [Concepts](concepts.md) |
| 3. Enroll a device with `meteorcli` | [Getting started → First device](getting-started.md#enroll-a-device) |

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
| Local / contributor setup | [Development](development.md) |
| Compose + Ansible (VM, EC2, on-prem) | [Deployment](deployment.md) |
| Kubernetes (existing cluster) | [Kubernetes](kubernetes.md) |
| AWS EC2 quick install | [Install quickstart](install-quickstart.md) |
| `installation.yaml` | [Installer configuration](installer-configuration.md) |
| Modular services (`cloud_app`, `vpn`) | [Services](services.md) |
| AWS prerequisites & deploy | [Prerequisites](aws-prerequisites.md) · [AWS deployment](aws-deployment.md) |
| Upgrades · destroy · logs | [Upgrades](upgrades.md) · [Destroy](destroy.md) · [Observability](observability.md) |
| When something breaks | [Troubleshooting](troubleshooting.md) |
| EC2 smoke test (CI) | [AWS CI](aws-ci.md) |

## Related code docs

- Device agent package: [`src/device-plane/agent/README.md`](../src/device-plane/agent/README.md)
- Installer package: [`infrastructure/installer/README.md`](../infrastructure/installer/README.md)
