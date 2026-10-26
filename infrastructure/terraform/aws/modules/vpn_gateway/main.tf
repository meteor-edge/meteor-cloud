# Dedicated WireGuard gateway: its own instance, security group, and Elastic IP.
# Ansible (deploy/ansible, `vpn_gateway` inventory group) installs and runs WireGuard.
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

resource "aws_security_group" "gateway" {
  name        = "${var.installation_name}-vpn-gateway"
  description = "WireGuard gateway for ${var.installation_name}"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "WireGuard devices"
    from_port   = var.listen_port
    to_port     = var.listen_port
    protocol    = "udp"
    cidr_blocks = var.allowed_client_cidrs
  }

  # Site link from the API host over the VPC (private IP), whatever allowed_client_cidrs is.
  dynamic "ingress" {
    for_each = var.platform_security_group_id != "" ? [1] : []
    content {
      description     = "WireGuard site link from the API host"
      from_port       = var.listen_port
      to_port         = var.listen_port
      protocol        = "udp"
      security_groups = [var.platform_security_group_id]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.installation_name}-vpn-gateway-sg"
  })
}

resource "aws_instance" "gateway" {
  ami                         = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu[0].id
  instance_type               = var.instance_type
  key_name                    = var.ssh_key_name
  vpc_security_group_ids      = [aws_security_group.gateway.id]
  availability_zone           = var.availability_zone != "" ? var.availability_zone : null
  associate_public_ip_address = !var.assign_elastic_ip

  root_block_device {
    volume_size = var.root_volume_size_gb
    volume_type = var.root_volume_type
  }

  tags = merge(var.tags, {
    Name = "${var.installation_name}-vpn-gateway"
  })

  # AWS reports true once the Elastic IP is attached, and a newer Ubuntu image appears every few
  # weeks; without this either one replaces the instance (and its data) on the next apply.
  # Set ami_id to move a host to a new image on purpose.
  lifecycle {
    ignore_changes = [associate_public_ip_address, ami]
  }
}

# Devices store the endpoint; a stable address keeps them connected across stop/start.
resource "aws_eip" "gateway" {
  count  = var.assign_elastic_ip ? 1 : 0
  domain = "vpc"

  tags = merge(var.tags, {
    Name = "${var.installation_name}-vpn-gateway-eip"
  })
}

resource "aws_eip_association" "gateway" {
  count         = var.assign_elastic_ip ? 1 : 0
  instance_id   = aws_instance.gateway.id
  allocation_id = aws_eip.gateway[0].id
}
