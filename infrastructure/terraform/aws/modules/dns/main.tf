# <fqdn> (console/API, HTTP-01) and *.<fqdn> (apps, DNS-01 wildcard).
resource "aws_route53_record" "platform" {
  zone_id = var.zone_id
  name    = var.fqdn
  type    = "A"
  ttl     = 60
  records = [var.ingress_ip]
}

resource "aws_route53_record" "apps" {
  zone_id = var.zone_id
  name    = "*.${var.fqdn}"
  type    = "A"
  ttl     = 60
  records = [var.ingress_ip]
}

# wg.<fqdn>: devices dial this name so they follow a new server IP.
resource "aws_route53_record" "wireguard" {
  count   = var.create_wireguard ? 1 : 0
  zone_id = var.zone_id
  name    = "wg.${var.fqdn}"
  type    = "A"
  ttl     = 60
  records = [coalesce(var.wireguard_ip, var.ingress_ip)]
}
