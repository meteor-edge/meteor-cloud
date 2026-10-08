variable "security_group_id" {
  type        = string
  description = "Security group to attach VPN ingress rules"
}

variable "listen_port" {
  type        = number
  default     = 51820
  description = "WireGuard UDP listen port"
}

variable "allowed_client_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDR blocks allowed to connect to the VPN"
}
