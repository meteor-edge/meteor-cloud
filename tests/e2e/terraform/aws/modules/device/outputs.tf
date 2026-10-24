output "public_ips" {
  value = aws_instance.device[*].public_ip
}

output "hosts" {
  value = [for d in aws_instance.device : {
    instance_id = d.id
    public_ip   = d.public_ip
    private_ip  = d.private_ip
  }]
}
