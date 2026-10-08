resource "aws_security_group_rule" "wireguard_udp" {
  type              = "ingress"
  from_port         = var.listen_port
  to_port           = var.listen_port
  protocol          = "udp"
  cidr_blocks       = var.allowed_client_cidrs
  security_group_id = var.security_group_id
  description       = "WireGuard VPN"
}
