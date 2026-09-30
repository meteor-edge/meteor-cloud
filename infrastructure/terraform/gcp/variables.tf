variable "installation_name" {
  type        = string
  description = "Installation name used for resource naming and labels"
}

variable "environment" {
  type        = string
  description = "Deployment environment"
}

variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  description = "GCP region for Cloud Run, Cloud SQL, and Memorystore"
}

variable "backend_image" {
  type        = string
  description = "Container image for the FastAPI backend (Artifact Registry, GHCR, or similar)"
}

variable "frontend_image" {
  type        = string
  description = "Container image for the nginx frontend"
}

variable "postgres_database" {
  type        = string
  default     = "edge_platform"
  description = "Cloud SQL database name"
}

variable "postgres_username" {
  type        = string
  default     = "edge_platform"
  description = "Cloud SQL database user"
}

variable "postgres_password" {
  type        = string
  sensitive   = true
  description = "Cloud SQL database password (from EDGE_PLATFORM_POSTGRES_PASSWORD)"
}

variable "jwt_secret" {
  type        = string
  sensitive   = true
  description = "JWT signing secret (from EDGE_PLATFORM_JWT_SECRET)"
}

variable "domain" {
  type        = string
  default     = ""
  description = "Optional custom domain for a Google-managed HTTPS certificate"
}

variable "public_url" {
  type        = string
  default     = ""
  description = "Optional public URL override used for CORS (otherwise LB IP or https://domain)"
}

variable "sql_tier" {
  type        = string
  default     = "db-f1-micro"
  description = "Cloud SQL machine tier"
}

variable "sql_disk_size_gb" {
  type        = number
  default     = 10
  description = "Cloud SQL disk size in GB"
}

variable "redis_memory_size_gb" {
  type        = number
  default     = 1
  description = "Memorystore Redis size in GB"
}

variable "deletion_protection" {
  type        = bool
  default     = false
  description = "Protect Cloud SQL from terraform destroy"
}

variable "min_instances" {
  type        = number
  default     = 0
  description = "Cloud Run minimum instances"
}

variable "max_instances" {
  type        = number
  default     = 4
  description = "Cloud Run maximum instances"
}

variable "backend_cpu" {
  type        = string
  default     = "1"
  description = "Cloud Run backend CPU"
}

variable "backend_memory" {
  type        = string
  default     = "1Gi"
  description = "Cloud Run backend memory"
}

variable "frontend_cpu" {
  type        = string
  default     = "1"
  description = "Cloud Run frontend CPU"
}

variable "frontend_memory" {
  type        = string
  default     = "512Mi"
  description = "Cloud Run frontend memory"
}

variable "create_artifact_registry" {
  type        = bool
  default     = true
  description = "Create an Artifact Registry Docker repository for app images"
}

variable "enable_apis" {
  type        = bool
  default     = true
  description = "Enable required Google APIs in the project"
}

variable "subnet_cidr" {
  type        = string
  default     = "10.20.0.0/24"
  description = "Subnet CIDR for Direct VPC egress (must be /26 or larger)"
}

variable "labels" {
  type        = map(string)
  default     = {}
  description = "Additional resource labels"
}
