# <fqdn> (console/API, HTTP-01) and *.<fqdn>: every published app, at any depth
# (<name>.<fqdn> and <app>.<device>.<org>.<fqdn>), with DNS-01 wildcard certificates.
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

# wg.<fqdn>: the WireGuard server devices dial (the API host, or the dedicated gateway).
# A name instead of an IP, so devices follow the server if its address changes.
resource "aws_route53_record" "wireguard" {
  zone_id = var.zone_id
  name    = "wg.${var.fqdn}"
  type    = "A"
  ttl     = 60
  records = [coalesce(var.wireguard_ip, var.ingress_ip)]
}
