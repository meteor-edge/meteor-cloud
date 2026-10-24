output "records" {
  value = [aws_route53_record.platform.fqdn, aws_route53_record.apps.fqdn, aws_route53_record.wireguard.fqdn]
}
