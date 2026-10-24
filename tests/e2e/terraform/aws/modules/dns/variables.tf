variable "zone_id" {
  type = string
}

variable "fqdn" {
  type = string
}

variable "ingress_ip" {
  type = string
}

variable "wireguard_ip" {
  description = "Where wg.<fqdn> points. Empty: the ingress IP (WireGuard on the API host or behind the same router)."
  type        = string
  default     = ""
}
