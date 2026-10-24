# Instance role for Traefik's Route53 DNS-01: it may only write the ACME TXT
# records of this environment in this zone.
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "dns01" {
  statement {
    sid       = "AcmeChallengeRecords"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${var.zone_id}"]

    # *.<fqdn> for custom app names and *.<device>.<org>.<fqdn> per device.
    condition {
      test     = "ForAllValues:StringLike"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values   = ["_acme-challenge.${var.record_fqdn}", "_acme-challenge.*.${var.record_fqdn}"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsRecordTypes"
      values   = ["TXT"]
    }
  }

  statement {
    sid       = "ReadZone"
    actions   = ["route53:ListResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${var.zone_id}"]
  }

  statement {
    sid       = "Discovery"
    actions   = ["route53:GetChange", "route53:ListHostedZonesByName", "route53:ListHostedZones"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "traefik" {
  name               = "${var.name}-traefik-dns01"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "dns01" {
  name   = "route53-dns01"
  role   = aws_iam_role.traefik.id
  policy = data.aws_iam_policy_document.dns01.json
}

resource "aws_iam_instance_profile" "traefik" {
  name = "${var.name}-traefik-dns01"
  role = aws_iam_role.traefik.name
}
