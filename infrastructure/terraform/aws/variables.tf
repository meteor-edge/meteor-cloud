variable "installation_name" {
  type        = string
  description = "Installation name used for tagging"
}

variable "environment" {
  type        = string
  description = "Deployment environment"
}

variable "enabled_services" {
  type        = list(string)
  description = "Services to provision (cloud_app, vpn, ...)"
  default     = ["cloud_app", "vpn"]
}

variable "aws_region" {
  type        = string
  description = "AWS region"
}

variable "aws_profile" {
  type        = string
  default     = ""
  description = "Optional AWS profile"
}

variable "availability_zone" {
  type        = string
  default     = ""
  description = "Optional availability zone"
}

variable "instance_type" {
  type        = string
  default     = "t3.small"
  description = "EC2 instance type"
}

variable "architecture" {
  type        = string
  default     = "amd64"
  description = "Instance architecture (amd64 or arm64)"
}

variable "ami_id" {
  type        = string
  default     = ""
  description = "Optional explicit AMI ID"
}

variable "ssh_key_name" {
  type        = string
  description = "EC2 SSH key pair name"
}

variable "root_volume_size_gb" {
  type        = number
  default     = 30
  description = "Root EBS volume size in GB"
}

variable "root_volume_type" {
  type        = string
  default     = "gp3"
  description = "Root EBS volume type"
}

variable "assign_elastic_ip" {
  type        = bool
  default     = true
  description = "Assign and associate an Elastic IP"
}

variable "allowed_ssh_cidrs" {
  type        = list(string)
  description = "CIDR blocks allowed for SSH"
}

variable "allow_http" {
  type        = bool
  default     = true
  description = "Allow HTTP from the internet"
}

variable "allow_https" {
  type        = bool
  default     = true
  description = "Allow HTTPS from the internet"
}

variable "vpn_listen_port" {
  type        = number
  default     = 51820
  description = "WireGuard UDP port when vpn service is enabled"
}

variable "vpn_allowed_client_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDR blocks allowed to connect to WireGuard"
}

variable "vpn_placement" {
  type        = string
  default     = "same_host"
  description = "Where WireGuard runs: same_host (cloud_app instance) or dedicated (own instance)"

  validation {
    condition     = contains(["same_host", "dedicated"], var.vpn_placement)
    error_message = "vpn_placement must be same_host or dedicated."
  }
}

variable "vpn_instance_type" {
  type        = string
  default     = "t3.micro"
  description = "EC2 instance type for a dedicated WireGuard gateway"
}

variable "vpn_root_volume_size_gb" {
  type        = number
  default     = 16
  description = "Root EBS volume size in GB for a dedicated WireGuard gateway"
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Additional resource tags"
}
