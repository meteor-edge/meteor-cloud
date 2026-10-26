output "control_plane" {
  value = {
    instance_id = module.cloud_app.instance_id
    public_ip   = module.cloud_app.public_ip
    private_ip  = module.cloud_app.private_ip
  }
}

output "gateway" {
  value = var.vpn_placement == "dedicated" ? {
    instance_id = module.vpn_gateway[0].instance_id
    public_ip   = module.vpn_gateway[0].public_ip
    private_ip  = module.vpn_gateway[0].private_ip
  } : null
}

output "devices" {
  value = module.devices.hosts
}

output "fqdn" {
  value = local.fqdn
}

output "dns_managed" {
  value = local.route53
}

output "ssh_user" {
  value = "ubuntu"
}
