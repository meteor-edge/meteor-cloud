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
