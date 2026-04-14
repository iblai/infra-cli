# ibl.ai Infrastructure -- Multi-Server Variables

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "staging"
}

variable "base_domain" {
  description = "Base domain for the application"
  type        = string
}

variable "bucket_suffix" {
  description = "Optional suffix for S3 bucket names to avoid collisions"
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "vpn_ip" {
  description = "IP address allowed SSH access (CIDR notation, e.g. 203.0.113.42/32)"
  type        = string
}

# ---------------------------------------------------------------------------
# App Servers
# ---------------------------------------------------------------------------

variable "app_server_count" {
  description = "Number of app servers behind the ALB"
  type        = number
  default     = 2

  validation {
    condition     = var.app_server_count >= 2 && var.app_server_count <= 10
    error_message = "App server count must be between 2 and 10."
  }
}

variable "app_server_instance_type" {
  description = "EC2 instance type for app servers"
  type        = string
  default     = "t3.2xlarge"
}

variable "app_server_volume_size" {
  description = "Root volume size for app servers in GB"
  type        = number
  default     = 250
}

variable "ami_id" {
  description = "Custom AMI ID (uses latest Ubuntu 22.04 if empty)"
  type        = string
  default     = ""
}

variable "skip_user_data" {
  description = "Skip user data bootstrap script (for custom AMIs)"
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Services Server
# ---------------------------------------------------------------------------

variable "services_instance_type" {
  description = "EC2 instance type for the services server"
  type        = string
  default     = "t3.2xlarge"
}

variable "services_volume_size" {
  description = "Root volume size for services server in GB"
  type        = number
  default     = 500
}

# ---------------------------------------------------------------------------
# SSH
# ---------------------------------------------------------------------------

variable "create_key_pair" {
  description = "Whether to create a new SSH key pair"
  type        = bool
  default     = true
}

variable "key_pair_name" {
  description = "Name for the new SSH key pair"
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  description = "SSH public key material for the new key pair"
  type        = string
  default     = ""
}

variable "existing_key_pair_name" {
  description = "Name of an existing AWS key pair to use"
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Certificates & DNS
# ---------------------------------------------------------------------------

variable "certificate_method" {
  description = "Certificate method: acm, upload, or none"
  type        = string
  default     = "none"
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID (required for ACM certificates)"
  type        = string
  default     = ""
}

variable "certificate_body_file" {
  description = "Path to certificate body PEM file (upload method)"
  type        = string
  default     = ""
}

variable "certificate_key_file" {
  description = "Path to certificate private key PEM file (upload method)"
  type        = string
  default     = ""
}

variable "certificate_chain_file" {
  description = "Path to certificate chain PEM file (upload method)"
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# MySQL RDS (optional)
# ---------------------------------------------------------------------------

variable "enable_mysql" {
  description = "Whether to create a managed MySQL RDS instance"
  type        = bool
  default     = false
}

variable "rds_mysql_instance_class" {
  description = "RDS instance class for MySQL"
  type        = string
  default     = "db.r6g.large"
}

variable "rds_mysql_storage_size" {
  description = "Storage size for MySQL RDS in GB"
  type        = number
  default     = 300
}

variable "mysql_database_name" {
  description = "MySQL database name"
  type        = string
  default     = "iblapp"
}

variable "mysql_username" {
  description = "MySQL master username"
  type        = string
  default     = "admin"
}

variable "mysql_password" {
  description = "MySQL master password"
  type        = string
  default     = ""
  sensitive   = true
}

# ---------------------------------------------------------------------------
# PostgreSQL RDS (optional)
# ---------------------------------------------------------------------------

variable "enable_postgres" {
  description = "Whether to create a managed PostgreSQL RDS instance"
  type        = bool
  default     = false
}

variable "rds_postgres_instance_class" {
  description = "RDS instance class for PostgreSQL"
  type        = string
  default     = "db.r6g.large"
}

variable "rds_postgres_storage_size" {
  description = "Storage size for PostgreSQL RDS in GB"
  type        = number
  default     = 300
}

variable "postgres_database_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "iblapp"
}

variable "postgres_username" {
  description = "PostgreSQL master username"
  type        = string
  default     = "postgres"
}

variable "postgres_password" {
  description = "PostgreSQL master password"
  type        = string
  default     = ""
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Redis ElastiCache (optional)
# ---------------------------------------------------------------------------

variable "enable_redis" {
  description = "Whether to create a managed Redis ElastiCache cluster"
  type        = bool
  default     = false
}

variable "redis_instance_type" {
  description = "ElastiCache Redis instance type"
  type        = string
  default     = "cache.r6g.xlarge"
}

variable "redis_auth_token" {
  description = "Auth token for Redis cluster (at least 16 characters)"
  type        = string
  default     = ""
  sensitive   = true
}
