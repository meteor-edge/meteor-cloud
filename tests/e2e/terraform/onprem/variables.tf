variable "env_name" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "dns_provider" {
  type = string
}

variable "dns_zone_id" {
  type = string
}

variable "domain" {
  type = string
}

variable "ingress_ip" {
  description = "Public address of the on-prem ingress (router port-forward or public IP)."
  type        = string
}
