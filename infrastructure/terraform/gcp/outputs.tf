output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "platform_url" {
  value = module.gcp_cloud_run.platform_url
}

output "load_balancer_ip" {
  value = module.gcp_cloud_run.load_balancer_ip
}

output "backend_service_url" {
  value = module.gcp_cloud_run.backend_service_url
}

output "frontend_service_url" {
  value = module.gcp_cloud_run.frontend_service_url
}

output "sql_connection_name" {
  value = module.gcp_cloud_run.sql_connection_name
}

output "artifact_registry_url" {
  value = module.gcp_cloud_run.artifact_registry_url
}

output "enabled_services" {
  value = ["cloud_app"]
}

# AWS-shaped aliases so the installer can reuse TerraformOutputs.
output "public_ip" {
  value = module.gcp_cloud_run.load_balancer_ip
}

output "elastic_ip" {
  value = module.gcp_cloud_run.load_balancer_ip
}

output "private_ip" {
  value = ""
}

output "instance_id" {
  value = module.gcp_cloud_run.backend_service_name
}

output "ssh_username" {
  value = ""
}

output "security_group_id" {
  value = ""
}
