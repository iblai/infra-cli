# ibl.ai Infrastructure — Call Server Outputs

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.main.id
}

output "elastic_ip" {
  description = "Elastic IP allocated to the call server. DNS should point here."
  value       = aws_eip.main.public_ip
}

output "instance_public_ip" {
  description = "Public IP of the call server (same as elastic_ip)"
  value       = aws_eip.main.public_ip
}

output "instance_private_ip" {
  description = "EC2 private IP"
  value       = aws_instance.main.private_ip
}

output "security_group_id" {
  description = "Call server security group ID"
  value       = aws_security_group.call.id
}

output "call_domain" {
  description = "FQDN operators should use to reach the call server (Route53-managed when hosted_zone_id set)"
  value       = var.base_domain
}

output "ssh_command" {
  description = "SSH connection command"
  value       = "ssh ubuntu@${aws_eip.main.public_ip}"
}

output "livekit_ws_url" {
  description = "Raw LiveKit WebSocket endpoint (front with a TLS terminator to serve wss:// on the base domain)"
  value       = "ws://${aws_eip.main.public_ip}:7880"
}
