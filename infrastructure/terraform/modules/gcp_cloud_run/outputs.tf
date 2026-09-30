output "platform_url" {
  value = local.use_https ? "https://${var.domain}" : "http://${google_compute_global_address.lb.address}"
}

output "load_balancer_ip" {
  value = google_compute_global_address.lb.address
}

output "backend_service_url" {
  value = google_cloud_run_v2_service.backend.uri
}

output "frontend_service_url" {
  value = google_cloud_run_v2_service.frontend.uri
}

output "backend_service_name" {
  value = google_cloud_run_v2_service.backend.name
}

output "sql_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "artifact_registry_url" {
  value = var.create_artifact_registry ? "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app[0].repository_id}" : ""
}
