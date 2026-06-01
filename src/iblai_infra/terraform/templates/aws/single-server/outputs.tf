# ibl.ai Infrastructure — Single Server Outputs

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

output "instance_public_ip" {
  description = "EC2 public IP"
  value       = aws_instance.main.public_ip
}

output "instance_private_ip" {
  description = "EC2 private IP"
  value       = aws_instance.main.private_ip
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.main.dns_name
}

output "alb_arn" {
  description = "ALB ARN"
  value       = aws_lb.main.arn
}

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

output "certificate_arn_1" {
  description = "ACM certificate 1 ARN (api/core services)"
  value       = var.certificate_method == "acm" ? aws_acm_certificate.main_1[0].arn : ""
}

output "certificate_arn_2" {
  description = "ACM certificate 2 ARN (auth/monitoring)"
  value       = var.certificate_method == "acm" ? aws_acm_certificate.main_2[0].arn : ""
}

output "ssh_command" {
  description = "SSH connection command"
  value       = "ssh ubuntu@${aws_instance.main.public_ip}"
}

output "application_url" {
  description = "Primary application URL"
  value = var.certificate_method != "none" ? (
    var.certificate_method == "acm" ? "https://learn.${var.base_domain}" : "https://${aws_lb.main.dns_name}"
  ) : "http://${aws_lb.main.dns_name}"
}

output "waf_web_acl_arn" {
  description = "WAFv2 Web ACL ARN (empty when WAF disabled)"
  value       = var.enable_waf ? aws_wafv2_web_acl.main[0].arn : ""
}

output "waf_ip_set_arn" {
  description = "WAFv2 admin IPSet ARN (empty when WAF disabled)"
  value       = var.enable_waf ? aws_wafv2_ip_set.admins[0].arn : ""
}
