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

Create in the target region:

```bash
aws ec2 create-key-pair \
  --region eu-central-1 \
  --key-name edge-platform \
  --query 'KeyMaterial' --output text > ~/.ssh/edge-platform.pem

chmod 600 ~/.ssh/edge-platform.pem
```

Reference in `installation.yaml`:

```yaml
aws:
  ssh_key_name: edge-platform
  ssh_private_key_path: ~/.ssh/edge-platform.pem
```

Ensure the PEM file is non-empty. AWS only returns the private key once at creation.

## IAM permissions

Terraform creates (per enabled service):

- **cloud_app**: EC2 instance, security group, Elastic IP, EBS volume, tags
- **vpn**: additional security group ingress rule (UDP WireGuard port)

Minimum actions: `ec2:RunInstances`, `ec2:CreateSecurityGroup`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, `ec2:AllocateAddress`, `ec2:AssociateAddress`, `ec2:CreateTags`, `ec2:Describe*`, `ec2:TerminateInstances`.

Scoped EC2 permissions in the deployment region are sufficient.

## Secrets

Export before `validate`, `plan`, or `apply`:

```bash
# Required when cloud_app is enabled
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'

# Optional
export EDGE_PLATFORM_ADMIN_EMAIL='admin@example.com'
export EDGE_PLATFORM_ADMIN_PASSWORD='...'
export EDGE_PLATFORM_ACME_EMAIL='ops@example.com'
export EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY='...'
export EDGE_PLATFORM_REDIS_PASSWORD='...'
```

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
make up
```

See [Install quickstart](install-quickstart.md) and [AWS deployment](aws-deployment.md).
