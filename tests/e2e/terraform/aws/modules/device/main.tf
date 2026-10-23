# Simulated edge devices: SSH from the operator only; no other inbound (devices dial out).
data "aws_ami" "noble" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_security_group" "device" {
  name        = "${var.name}-device"
  description = "MeteorCloud E2E devices: SSH from the operator only"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-device-sg" }
}

resource "aws_instance" "device" {
  count                  = var.device_count
  ami                    = data.aws_ami.noble.id
  instance_type          = var.instance_type
  key_name               = var.ssh_key_name
  vpc_security_group_ids = [aws_security_group.device.id]

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_size = 12
    volume_type = "gp3"
    encrypted   = true
  }

  tags = { Name = "${var.name}-device-${count.index + 1}" }

  # A newer Ubuntu image must not rebuild a configured device on the next apply.
  lifecycle {
    ignore_changes = [ami]
  }
}
