data "aws_ami" "ubuntu" {
  count       = var.ami_id == "" ? 1 : 0
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-${var.architecture}-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "platform" {
  name        = "${var.installation_name}-platform"
  description = "Edge Platform security group for ${var.installation_name}"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  dynamic "ingress" {
    for_each = var.allow_http ? [1] : []
    content {
      description = "HTTP"
      from_port   = 80
      to_port     = 80
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  dynamic "ingress" {
    for_each = var.allow_https ? [1] : []
    content {
      description = "HTTPS"
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  dynamic "ingress" {
    for_each = var.extra_ingress
    content {
      description = ingress.value.description
      from_port   = ingress.value.from_port
      to_port     = ingress.value.to_port
      protocol    = ingress.value.protocol
      cidr_blocks = ingress.value.cidr_blocks
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.installation_name}-platform-sg"
  })
}

resource "aws_instance" "platform" {
  ami                         = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu[0].id
  instance_type               = var.instance_type
  key_name                    = var.ssh_key_name
  vpc_security_group_ids      = [aws_security_group.platform.id]
  availability_zone           = var.availability_zone != "" ? var.availability_zone : null
  associate_public_ip_address = !var.assign_elastic_ip
  iam_instance_profile        = var.iam_instance_profile != "" ? var.iam_instance_profile : null

  # IMDSv2 only. A hop limit of 2 lets containers (Traefik DNS-01) use the instance role;
  # meteorcloud-imds-guard (platform_deployment role) blocks every container but Traefik.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = var.metadata_hop_limit
  }

  root_block_device {
    volume_size = var.root_volume_size_gb
    volume_type = var.root_volume_type
  }

  tags = merge(var.tags, {
    Name = var.instance_name != "" ? var.instance_name : var.installation_name
  })

  # AWS reports true once the Elastic IP is attached, and a newer Ubuntu image appears every few
  # weeks; without this either one replaces the instance (and its data) on the next apply.
  # Set ami_id to move a host to a new image on purpose.
  lifecycle {
    ignore_changes = [associate_public_ip_address, ami]
  }
}

resource "aws_eip" "platform" {
  count  = var.assign_elastic_ip ? 1 : 0
  domain = "vpc"

  tags = merge(var.tags, {
    Name = "${var.installation_name}-eip"
  })
}

resource "aws_eip_association" "platform" {
  count         = var.assign_elastic_ip ? 1 : 0
  instance_id   = aws_instance.platform.id
  allocation_id = aws_eip.platform[0].id
}
