provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null
}

module "cloud_app" {
  count  = contains(var.enabled_services, "cloud_app") ? 1 : 0
  source = "./modules/cloud_app"

  installation_name   = var.installation_name
  environment         = var.environment
  aws_region          = var.aws_region
  availability_zone   = var.availability_zone
  instance_type       = var.instance_type
  architecture        = var.architecture
  ami_id              = var.ami_id
  ssh_key_name        = var.ssh_key_name
  root_volume_size_gb = var.root_volume_size_gb
  root_volume_type    = var.root_volume_type
  assign_elastic_ip   = var.assign_elastic_ip
  allowed_ssh_cidrs   = var.allowed_ssh_cidrs
  allow_http          = var.allow_http
  allow_https         = var.allow_https
  tags                = var.tags
}

# vpn_placement = "same_host": WireGuard on the cloud_app instance (UDP rule on its SG).
module "vpn" {
  count  = contains(var.enabled_services, "vpn") && contains(var.enabled_services, "cloud_app") && var.vpn_placement == "same_host" ? 1 : 0
  source = "./modules/vpn"

  security_group_id    = module.cloud_app[0].security_group_id
  listen_port          = var.vpn_listen_port
  allowed_client_cidrs = var.vpn_allowed_client_cidrs
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
