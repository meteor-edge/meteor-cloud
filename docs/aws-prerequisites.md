# AWS deployment prerequisites

## Local tools

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.13+ | Installer |
| Terraform | 1.5+ | AWS infrastructure |
| Ansible | 2.15+ | Host configuration |
| OpenSSH | any recent | SSH to EC2 |
| AWS CLI | recommended | Verify credentials |

Install the installer:

```bash
cd infrastructure/installer && pip install -e ".[dev]"
```

## AWS authentication

One of:

- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (+ optional `AWS_SESSION_TOKEN`)
- `~/.aws/credentials`
- Named profile via `aws.profile` in `installation.yaml`

Verify:

```bash
aws sts get-caller-identity
```

## SSH key pair

`aws.ssh_key_name` (EC2 key pair in the region) and `aws.ssh_private_key_path` (local PEM) **must be the same key**. `make aws-up` checks that before Terraform when the AWS CLI is available; a mismatch used to create the instance and then fail at SSH.

Create a pair in the target region (AWS returns the private key **once**):

```bash
aws ec2 create-key-pair \
  --region eu-central-1 \
  --key-name edge-platform \
  --query 'KeyMaterial' --output text > ~/.ssh/edge-platform.pem

chmod 600 ~/.ssh/edge-platform.pem
```

Or import a PEM you already have:

```bash
aws ec2 import-key-pair \
  --region eu-central-1 \
  --key-name edge-platform-local \
  --public-key-material "$(ssh-keygen -y -f ~/.ssh/edge-platform.pem | base64 -w0)"
```

Reference in `installation.yaml`:

```yaml
aws:
  ssh_key_name: edge-platform          # or edge-platform-local after import
  ssh_private_key_path: ~/.ssh/edge-platform.pem
```

## IAM permissions

Terraform creates (per enabled service):

- **cloud_app**: EC2 instance, security group, Elastic IP, EBS volume, tags
- **vpn**: additional security group ingress rule (UDP WireGuard port)

Minimum actions: `ec2:RunInstances`, `ec2:CreateSecurityGroup`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, `ec2:AllocateAddress`, `ec2:AssociateAddress`, `ec2:CreateTags`, `ec2:Describe*`, `ec2:TerminateInstances`.

Scoped EC2 permissions in the deployment region are sufficient.

## Secrets

`make aws-up` and `make plan` generate these if they are unset (and print them
after `make aws-up`): `EDGE_PLATFORM_POSTGRES_PASSWORD`,
`EDGE_PLATFORM_JWT_SECRET`, `EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN`. Saved
at `~/.meteorcloud/secrets.env`. Export a value first to keep your own.

Calling `edge-installer` directly still requires the env vars.

Optional: `EDGE_PLATFORM_ADMIN_EMAIL`, `EDGE_PLATFORM_ADMIN_PASSWORD`,
`EDGE_PLATFORM_ACME_EMAIL`, `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY`,
`EDGE_PLATFORM_REDIS_PASSWORD`.

Generate a WireGuard private key:

```bash
wg genkey   # use output as EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY
```

## Configuration file

Copy the example and edit:

```bash
cp infrastructure/installer/edge_installer/config/examples/installation.yaml ./installation.yaml
```

Set `network.allowed_ssh_cidrs` to your public IP `/32` (or wider for testing).

## Deploy

```bash
make aws-up
```

See [Install quickstart](install-quickstart.md) and [AWS deployment](aws-deployment.md).
