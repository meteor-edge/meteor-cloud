output "instance_id" {
  value = aws_instance.gateway.id
}

output "public_ip" {
  value = var.assign_elastic_ip ? aws_eip.gateway[0].public_ip : aws_instance.gateway.public_ip
}

output "private_ip" {
  value = aws_instance.gateway.private_ip
}

output "security_group_id" {
  value = aws_security_group.gateway.id
}
