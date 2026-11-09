# <fqdn> (console/API, HTTP-01) and *.<fqdn> (apps, DNS-01 wildcard).
# allow_overwrite: a prior install or manual A record for the same name must not
# block apply (common when reusing a domain after destroy or domain rename).
resource "aws_route53_record" "platform" {
  zone_id         = var.zone_id
  name            = var.fqdn
  type            = "A"
  ttl             = 60
  records         = [var.ingress_ip]
  allow_overwrite = true
}

resource "aws_route53_record" "apps" {
  zone_id         = var.zone_id
  name            = "*.${var.fqdn}"
  type            = "A"
  ttl             = 60
  records         = [var.ingress_ip]
  allow_overwrite = true
}

# wg.<fqdn>: devices dial this name so they follow a new server IP.
resource "aws_route53_record" "wireguard" {
  count           = var.create_wireguard ? 1 : 0
  zone_id         = var.zone_id
  name            = "wg.${var.fqdn}"
  type            = "A"
  ttl             = 60
  records         = [coalesce(var.wireguard_ip, var.ingress_ip)]
  allow_overwrite = true
}
