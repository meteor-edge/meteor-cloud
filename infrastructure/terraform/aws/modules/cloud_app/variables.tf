variable "installation_name" {
  type = string
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
