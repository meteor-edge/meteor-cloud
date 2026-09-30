variable "installation_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "backend_image" {
  type = string
}

variable "frontend_image" {
  type = string
}

variable "postgres_database" {
  type = string
}

variable "postgres_username" {
  type = string
}

variable "postgres_password" {
  type      = string
  sensitive = true
}

variable "jwt_secret" {
  type      = string
  sensitive = true
}

variable "domain" {
  type    = string
  default = ""
}

variable "public_url" {
  type    = string
  default = ""
}

variable "sql_tier" {
  type = string
}

variable "sql_disk_size_gb" {
  type = number
}

variable "redis_memory_size_gb" {
  type = number
}

variable "deletion_protection" {
  type = bool
}

variable "min_instances" {
  type = number
}

variable "max_instances" {
  type = number
}

variable "backend_cpu" {
  type = string
}

variable "backend_memory" {
  type = string
}

variable "frontend_cpu" {
  type = string
}

variable "frontend_memory" {
  type = string
}

variable "create_artifact_registry" {
  type = bool
}

variable "enable_apis" {
  type = bool
}

variable "subnet_cidr" {
  type = string
}

variable "labels" {
  type    = map(string)
  default = {}
}

locals {
  labels = merge(var.labels, {
    installation = var.installation_name
    environment  = var.environment
    managed-by   = "edge-installer"
    platform     = "edge-platform"
  })

  use_https = var.domain != ""

  cors_origin = trimspace(var.public_url) != "" ? trimsuffix(var.public_url, "/") : (
    local.use_https ? "https://${var.domain}" : "http://${google_compute_global_address.lb.address}"
  )

  encoded_db_password = urlencode(var.postgres_password)
  database_url        = "postgresql+psycopg://${var.postgres_username}:${local.encoded_db_password}@/${var.postgres_database}?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
}
