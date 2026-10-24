# On-prem: the hosts already exist; Terraform manages only the public DNS records.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project        = "meteorcloud-e2e"
      E2EEnvironment = var.env_name
      ManagedBy      = "terraform"
    }
  }
}

module "dns" {
  count  = var.dns_provider == "route53" ? 1 : 0
  source = "../aws/modules/dns"

  zone_id    = var.dns_zone_id
  fqdn       = "${var.env_name}.${var.domain}"
  ingress_ip = var.ingress_ip
}

output "fqdn" {
  value = "${var.env_name}.${var.domain}"
}
