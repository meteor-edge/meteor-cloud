provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

locals {
  name = "meteorcloud-${var.env_name}"
  # down and the cleanup audit find resources by these tags.
  tags = {
    Project        = "meteorcloud-e2e"
    E2EEnvironment = var.env_name
    ManagedBy      = "terraform"
  }
  fqdn    = var.domain != "" ? "${var.env_name}.${var.domain}" : ""
  route53 = var.dns_provider == "route53" && var.dns_zone_id != "" && var.domain != ""
}

resource "aws_key_pair" "e2e" {
  key_name   = local.name
  public_key = var.ssh_public_key
}

# Traefik's Route53 DNS-01 for the wildcard app certificate, via the instance role (no static keys).
module "dns_role" {
  count  = local.route53 ? 1 : 0
  source = "./modules/dns_role"

  name        = local.name
  zone_id     = var.dns_zone_id
  record_fqdn = local.fqdn
}

module "cloud_app" {
  source = "../../../../infrastructure/terraform/aws/modules/cloud_app"

  installation_name    = local.name
  instance_name        = "${local.name}-server"
  environment          = "e2e"
  aws_region           = var.aws_region
  instance_type        = var.control_plane_instance_type
  ssh_key_name         = aws_key_pair.e2e.key_name
  root_volume_size_gb  = 30
  allowed_ssh_cidrs    = var.allowed_ssh_cidrs
  iam_instance_profile = local.route53 ? module.dns_role[0].instance_profile_name : ""
  # Traefik reaches the instance role through IMDSv2 from inside a container.
  metadata_hop_limit = 2
  # Devices dial WireGuard on this host; only their addresses may reach UDP 51820.
  extra_ingress = var.vpn_placement == "same_host" ? [{
    description = "WireGuard VPN"
    from_port   = 51820
    to_port     = 51820
    protocol    = "udp"
    cidr_blocks = local.device_cidrs
  }] : []
  tags = local.tags
}

module "devices" {
  source = "./modules/device"

  name              = local.name
  device_count      = var.device_count
  instance_type     = var.device_instance_type
  ssh_key_name      = aws_key_pair.e2e.key_name
  allowed_ssh_cidrs = var.allowed_ssh_cidrs
}

# Devices dial the gateway; only their addresses may reach UDP 51820.
locals {
  device_cidrs = [for ip in module.devices.public_ips : "${ip}/32"]
}

module "vpn_gateway" {
  count  = var.vpn_placement == "dedicated" ? 1 : 0
  source = "../../../../infrastructure/terraform/aws/modules/vpn_gateway"

  installation_name          = local.name
  instance_type              = var.gateway_instance_type
  ssh_key_name               = aws_key_pair.e2e.key_name
  allowed_ssh_cidrs          = var.allowed_ssh_cidrs
  allowed_client_cidrs       = local.device_cidrs
  platform_security_group_id = module.cloud_app.security_group_id
  tags                       = local.tags
}

module "dns" {
  count  = local.route53 ? 1 : 0
  source = "./modules/dns"

  zone_id      = var.dns_zone_id
  fqdn         = local.fqdn
  ingress_ip   = module.cloud_app.public_ip
  wireguard_ip = var.vpn_placement == "dedicated" ? module.vpn_gateway[0].public_ip : module.cloud_app.public_ip
}
