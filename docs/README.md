# Documentation

Canonical public docs are served by the **website** (`/docs`). This tree is the Markdown source for Git and for anyone who prefers files over the site.

## Product

| Doc | Topic |
| --- | --- |
| [Architecture](architecture.md) | Modules, HTTP/MQTT split, providers |
| [Development](development.md) | Local Compose, env, coding standards |
| [Identity and organizations](identity-and-organizations.md) | Users, tenants, RBAC |
| [Modular installer services](services.md) | `cloud_app` and `vpn` |

## Fleet

| Doc | Topic |
| --- | --- |
| [Device types and groups](fleet/device-types.md) | Catalog |
| [Registration tokens](fleet/registration-tokens.md) | Admin-initiated enroll |
| [Device registration](fleet/device-registration.md) | Agent register |
| [Enrollment API keys](fleet/enrollment-api-keys.md) | Device-initiated enroll |
| [Device-initiated enrollment](fleet/device-request-enrollment.md) | Request / approve / claim |
| [Device authentication](fleet/device-authentication.md) | HTTP device token |
| [Heartbeat](fleet/heartbeat.md) | Last-seen |
| [MQTT](fleet/mqtt.md) | TLS, ACL, ingest |

## Operations

| Doc | Topic |
| --- | --- |
| [Install quickstart](install-quickstart.md) | AWS or GCP |
| [Installer configuration](installer-configuration.md) | `installation.yaml` |
| [AWS prerequisites](aws-prerequisites.md) | Accounts and keys |
| [AWS deployment](aws-deployment.md) | EC2 path |
| [GCP Cloud Run](gcp-deployment.md) | Cloud Run path |
| [Upgrades](upgrades.md) | |
| [Destroy](destroy.md) | |
| [Observability](observability.md) | |
| [Troubleshooting](troubleshooting.md) | |
| [AWS CI](aws-ci.md) | Throwaway EC2 workflow |

## Related

- [Frontends (console in this repo, private website)](frontends.md)
- Device agent: [`src/device-plane/agent/README.md`](../src/device-plane/agent/README.md)
- MQTT HTTP contract: [`contracts/mqtt-http.md`](../contracts/mqtt-http.md)
- Installer: [`infrastructure/installer/README.md`](../infrastructure/installer/README.md)
