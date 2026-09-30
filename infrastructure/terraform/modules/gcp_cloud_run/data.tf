resource "random_id" "sql" {
  byte_length = 3
}

resource "google_sql_database_instance" "main" {
  name             = "${var.installation_name}-pg-${random_id.sql.hex}"
  database_version = "POSTGRES_16"
  region           = var.region
  project          = var.project_id

  deletion_protection = var.deletion_protection

  settings {
    tier              = var.sql_tier
    availability_type = "ZONAL"
    disk_size         = var.sql_disk_size_gb
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.vpc.id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = var.environment == "production"
      point_in_time_recovery_enabled = var.environment == "production"
    }

    insights_config {
      query_insights_enabled = false
    }

    user_labels = local.labels
  }

  depends_on = [google_service_networking_connection.private_vpc]
}

resource "google_sql_database" "app" {
  name     = var.postgres_database
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = var.postgres_username
  instance = google_sql_database_instance.main.name
  password = var.postgres_password
}

resource "google_redis_instance" "cache" {
  name               = "${var.installation_name}-redis"
  tier               = "BASIC"
  memory_size_gb     = var.redis_memory_size_gb
  region             = var.region
  authorized_network = google_compute_network.vpc.id
  redis_version      = "REDIS_7_0"
  display_name       = "${var.installation_name} redis"
  auth_enabled       = true
  labels             = local.labels

  depends_on = [google_project_service.services]
}
