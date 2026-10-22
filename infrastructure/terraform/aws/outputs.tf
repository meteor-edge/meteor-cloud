output "instance_id" {
  value = length(module.cloud_app) > 0 ? module.cloud_app[0].instance_id : ""
}

output "public_ip" {
  value = length(module.cloud_app) > 0 ? module.cloud_app[0].public_ip : ""
}

output "elastic_ip" {
  value = length(module.cloud_app) > 0 ? module.cloud_app[0].elastic_ip : ""
}

output "private_ip" {
  value = length(module.cloud_app) > 0 ? module.cloud_app[0].private_ip : ""
}

output "region" {
  value = var.aws_region
}

output "ssh_username" {
  value = "ubuntu"
}

output "security_group_id" {
  value = length(module.cloud_app) > 0 ? module.cloud_app[0].security_group_id : ""
}

output "enabled_services" {
  value = var.enabled_services
}

output "vpn_listen_port" {
  value = length(module.vpn) > 0 ? module.vpn[0].listen_port : var.vpn_listen_port
}

output "vpn_placement" {
  value = var.vpn_placement
}

output "vpn_gateway_instance_id" {
  value = length(module.vpn_gateway) > 0 ? module.vpn_gateway[0].instance_id : ""
}

output "vpn_gateway_public_ip" {
  value = length(module.vpn_gateway) > 0 ? module.vpn_gateway[0].public_ip : ""
}

output "vpn_gateway_private_ip" {
  value = length(module.vpn_gateway) > 0 ? module.vpn_gateway[0].private_ip : ""
}
