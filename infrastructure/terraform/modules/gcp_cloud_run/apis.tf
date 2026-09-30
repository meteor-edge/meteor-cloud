resource "google_project_service" "services" {
  for_each = var.enable_apis ? toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "iam.googleapis.com",
  ]) : toset([])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  count         = var.create_artifact_registry ? 1 : 0
  location      = var.region
  repository_id = "${var.installation_name}-app"
  description   = "Edge Platform container images for ${var.installation_name}"
  format        = "DOCKER"
  labels        = local.labels

  depends_on = [google_project_service.services]
}
