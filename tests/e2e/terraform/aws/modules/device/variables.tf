variable "name" {
  type = string
}

variable "device_count" {
  type = number
}

variable "instance_type" {
  type = string
}

variable "ssh_key_name" {
  type = string
}

variable "allowed_ssh_cidrs" {
  type = list(string)
}
