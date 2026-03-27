# ibl.ai Infrastructure — Single Server Variables

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
  default     = "prod"
}

variable "base_domain" {
  description = "Base domain for the application"
  type        = string
}

variable "bucket_suffix" {
  description = "Optional suffix for S3 bucket names (e.g. date stamp for uniqueness)"
  type        = string
  default     = ""
}

# --- Network ---

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "vpn_ip" {
  description = "IP address allowed SSH access (port 22)"
  type        = string
}

# --- Compute ---

variable "ami_id" {
  description = "Custom AMI ID. When set, uses this AMI instead of the default Ubuntu lookup."
  type        = string
  default     = ""
}

variable "skip_user_data" {
  description = "Skip the user_data bootstrap script (set true for pre-built AMIs)"
  type        = bool
  default     = false
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.2xlarge"
}

variable "root_volume_size" {
  description = "Root volume size in GB"
  type        = number
  default     = 50
}

variable "root_volume_type" {
  description = "Root volume type"
  type        = string
  default     = "gp3"
}

# --- SSH Key ---

variable "create_key_pair" {
  description = "Whether to create a new key pair (true) or use an existing AWS key pair (false)"
  type        = bool
  default     = true
}

variable "key_pair_name" {
  description = "Name for the key pair (used when create_key_pair = true)"
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  description = "SSH public key material (used when create_key_pair = true)"
  type        = string
  default     = ""
}

variable "existing_key_pair_name" {
  description = "Name of an existing AWS key pair (used when create_key_pair = false)"
  type        = string
  default     = ""
}

# --- Certificates ---

variable "certificate_method" {
  description = "Certificate method: acm, upload, or none"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["acm", "upload", "none"], var.certificate_method)
    error_message = "certificate_method must be 'acm', 'upload', or 'none'"
  }
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID (required when certificate_method = acm)"
  type        = string
  default     = ""
}

variable "certificate_body_file" {
  description = "Path to certificate body PEM file (when certificate_method = upload)"
  type        = string
  default     = ""
}

variable "certificate_key_file" {
  description = "Path to certificate private key PEM file (when certificate_method = upload)"
  type        = string
  default     = ""
}

variable "certificate_chain_file" {
  description = "Path to certificate chain PEM file (when certificate_method = upload)"
  type        = string
  default     = ""
}
