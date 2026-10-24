variable "installation_name" {
  type = string
}

variable "extra_ingress" {
  description = "More ingress rules (e.g. WireGuard). Declared inline: an aws_security_group_rule on this group would be removed by the next apply."
  type = list(object({
    description = string
    from_port   = number
    to_port     = number
    protocol    = string
    cidr_blocks = list(string)
  }))
  default = []
}

variable "instance_name" {
  description = "EC2 Name tag; defaults to installation_name."
  type        = string
  default     = ""
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "availability_zone" {
  type    = string
  default = ""
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "architecture" {
  type    = string
  default = "amd64"
}

variable "ami_id" {
  type    = string
  default = ""
}

variable "ssh_key_name" {
  type = string
}

variable "root_volume_size_gb" {
  type    = number
  default = 30
}

variable "root_volume_type" {
  type    = string
  default = "gp3"
}

variable "assign_elastic_ip" {
  type    = bool
  default = true
}

variable "allowed_ssh_cidrs" {
  type = list(string)
}

variable "allow_http" {
  type    = bool
  default = true
}

variable "allow_https" {
  type    = bool
  default = true
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "iam_instance_profile" {
  type        = string
  default     = ""
  description = "Optional IAM instance profile (for example Route53 access for Traefik DNS-01)"
}

variable "metadata_hop_limit" {
  type        = number
  default     = 1
  description = "IMDSv2 hop limit; 2 lets containers on the host use the instance role"
}
