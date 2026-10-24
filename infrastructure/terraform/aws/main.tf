provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null
}

locals {
  route53 = var.dns_zone_id != "" && var.platform_domain != ""
}

module "dns_role" {
  count  = local.route53 ? 1 : 0
  source = "./modules/dns_role"

  name        = var.installation_name
  zone_id     = var.dns_zone_id
  record_fqdn = var.platform_domain
}

module "cloud_app" {
  count  = contains(var.enabled_services, "cloud_app") ? 1 : 0
  source = "./modules/cloud_app"

  installation_name    = var.installation_name
  environment          = var.environment
  aws_region           = var.aws_region
  availability_zone    = var.availability_zone
  instance_type        = var.instance_type
  architecture         = var.architecture
  ami_id               = var.ami_id
  ssh_key_name         = var.ssh_key_name
  root_volume_size_gb  = var.root_volume_size_gb
  root_volume_type     = var.root_volume_type
  assign_elastic_ip    = var.assign_elastic_ip
  allowed_ssh_cidrs    = var.allowed_ssh_cidrs
  allow_http           = var.allow_http
  allow_https          = var.allow_https
  iam_instance_profile = local.route53 ? module.dns_role[0].instance_profile_name : ""
  metadata_hop_limit   = local.route53 ? 2 : 1
  # vpn_placement = "same_host": WireGuard runs on this instance.
  extra_ingress = contains(var.enabled_services, "vpn") && var.vpn_placement == "same_host" ? [{
    description = "WireGuard VPN"
    from_port   = var.vpn_listen_port
    to_port     = var.vpn_listen_port
    protocol    = "udp"
    cidr_blocks = var.vpn_allowed_client_cidrs
  }] : []
  tags = var.tags
}

# The WireGuard rule used to be a separate aws_security_group_rule, which the group's inline
# rules removed on every second apply. Forget it without revoking: the inline rule above owns it now.
removed {
  from = module.vpn

  lifecycle {
    destroy = false
  }
}

# vpn_placement = "dedicated": WireGuard on its own instance; the API host joins it
# over a WireGuard site link (configured by Ansible).
module "vpn_gateway" {
  count  = contains(var.enabled_services, "vpn") && var.vpn_placement == "dedicated" ? 1 : 0
  source = "./modules/vpn_gateway"

  installation_name          = var.installation_name
  availability_zone          = var.availability_zone
  instance_type              = var.vpn_instance_type
  architecture               = var.architecture
  ami_id                     = var.ami_id
  ssh_key_name               = var.ssh_key_name
  root_volume_size_gb        = var.vpn_root_volume_size_gb
  root_volume_type           = var.root_volume_type
  assign_elastic_ip          = var.assign_elastic_ip
  allowed_ssh_cidrs          = var.allowed_ssh_cidrs
  listen_port                = var.vpn_listen_port
  allowed_client_cidrs       = var.vpn_allowed_client_cidrs
  platform_security_group_id = length(module.cloud_app) > 0 ? module.cloud_app[0].security_group_id : ""
  tags                       = var.tags
}

module "dns" {
  count  = local.route53 && length(module.cloud_app) > 0 ? 1 : 0
  source = "./modules/dns"

  zone_id          = var.dns_zone_id
  fqdn             = var.platform_domain
  ingress_ip       = module.cloud_app[0].public_ip
  wireguard_ip     = length(module.vpn_gateway) > 0 ? module.vpn_gateway[0].public_ip : module.cloud_app[0].public_ip
  create_wireguard = contains(var.enabled_services, "vpn")
}
