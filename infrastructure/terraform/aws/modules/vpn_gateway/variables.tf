variable "installation_name" {
  type = string
}

variable "availability_zone" {
  type    = string
  default = ""
}

variable "instance_type" {
  type    = string
  default = "t3.micro"
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
  default = 16
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

variable "listen_port" {
  type    = number
  default = 51820
}

variable "allowed_client_cidrs" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}

variable "platform_security_group_id" {
  type        = string
  default     = ""
  description = "API host security group allowed to reach the gateway for the site link"
}

variable "tags" {
  type    = map(string)
  default = {}
}
