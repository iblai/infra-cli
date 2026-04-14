# ibl.ai Infrastructure -- Multi-Server Outputs

# ---------------------------------------------------------------------------
# VPC & Network
# ---------------------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}

# ---------------------------------------------------------------------------
# App Servers
# ---------------------------------------------------------------------------

output "app_server_ids" {
  description = "App server instance IDs"
  value       = aws_instance.app_servers[*].id
}

output "app_server_public_ips" {
  description = "App server public IPs"
  value       = aws_instance.app_servers[*].public_ip
}

output "app_server_private_ips" {
  description = "App server private IPs"
  value       = aws_instance.app_servers[*].private_ip
}

# Backward-compat singular outputs (point at first app server)
output "instance_id" {
  description = "EC2 instance ID (first app server)"
  value       = aws_instance.app_servers[0].id
}

output "instance_public_ip" {
  description = "EC2 public IP (first app server)"
  value       = aws_instance.app_servers[0].public_ip
}

output "instance_private_ip" {
  description = "EC2 private IP (first app server)"
  value       = aws_instance.app_servers[0].private_ip
}

# ---------------------------------------------------------------------------
# Services Server
# ---------------------------------------------------------------------------

output "services_server_id" {
  description = "Services server instance ID"
  value       = aws_instance.services.id
}

output "services_server_private_ip" {
  description = "Services server private IP"
  value       = aws_instance.services.private_ip
}

# ---------------------------------------------------------------------------
# Load Balancer
# ---------------------------------------------------------------------------

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.main.dns_name
}

output "alb_arn" {
  description = "ALB ARN"
  value       = aws_lb.main.arn
}

# ---------------------------------------------------------------------------
# S3 Buckets
# ---------------------------------------------------------------------------

output "s3_bucket_backups" {
  description = "Backups S3 bucket"
  value       = aws_s3_bucket.backups.id
}

output "s3_bucket_media" {
  description = "Media S3 bucket"
  value       = aws_s3_bucket.dm_media.id
}

output "s3_bucket_static" {
  description = "Static S3 bucket"
  value       = aws_s3_bucket.dm_static.id
}

# ---------------------------------------------------------------------------
# EFS
# ---------------------------------------------------------------------------

output "efs_file_system_id" {
  description = "EFS file system ID for shared media"
  value       = aws_efs_file_system.media.id
}

output "efs_dns_name" {
  description = "EFS DNS name"
  value       = aws_efs_file_system.media.dns_name
}

# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

output "certificate_arn_1" {
  description = "ACM certificate 1 ARN (api/core services)"
  value       = var.certificate_method == "acm" ? aws_acm_certificate.main_1[0].arn : ""
}

output "certificate_arn_2" {
  description = "ACM certificate 2 ARN (auth/monitoring)"
  value       = var.certificate_method == "acm" ? aws_acm_certificate.main_2[0].arn : ""
}

# ---------------------------------------------------------------------------
# Databases (conditional)
# ---------------------------------------------------------------------------

output "mysql_endpoint" {
  description = "MySQL RDS endpoint"
  value       = var.enable_mysql ? aws_db_instance.mysql[0].endpoint : ""
}

output "mysql_port" {
  description = "MySQL port"
  value       = var.enable_mysql ? tostring(aws_db_instance.mysql[0].port) : ""
}

output "postgres_endpoint" {
  description = "PostgreSQL RDS endpoint"
  value       = var.enable_postgres ? aws_db_instance.postgres[0].endpoint : ""
}

output "postgres_port" {
  description = "PostgreSQL port"
  value       = var.enable_postgres ? tostring(aws_db_instance.postgres[0].port) : ""
}

# ---------------------------------------------------------------------------
# Redis (conditional)
# ---------------------------------------------------------------------------

output "redis_endpoint" {
  description = "Redis primary endpoint"
  value       = var.enable_redis ? aws_elasticache_replication_group.redis[0].primary_endpoint_address : ""
}

output "redis_port" {
  description = "Redis port"
  value       = var.enable_redis ? tostring(aws_elasticache_replication_group.redis[0].port) : ""
}

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

output "ssh_command" {
  description = "SSH command (first app server)"
  value       = "ssh ubuntu@${aws_instance.app_servers[0].public_ip}"
}

output "ssh_commands" {
  description = "SSH commands for all servers"
  value = {
    app_servers = [
      for i in aws_instance.app_servers : "ssh ubuntu@${i.public_ip}"
    ]
    services_server = "ssh -o ProxyJump=ubuntu@${aws_instance.app_servers[0].public_ip} ubuntu@${aws_instance.services.private_ip}"
  }
}

output "server_count" {
  description = "Number of app servers"
  value       = var.app_server_count
}

output "application_url" {
  description = "Primary application URL"
  value = var.certificate_method != "none" ? (
    var.certificate_method == "acm" ? "https://learn.${var.base_domain}" : "https://${aws_lb.main.dns_name}"
  ) : "http://${aws_lb.main.dns_name}"
}
