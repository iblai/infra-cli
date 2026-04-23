# ibl.ai Infrastructure — Call Server (LiveKit) Variables

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
  description = "Fully qualified domain for the call server (e.g. call.example.com). A Route53 A record is created pointing at the Elastic IP when hosted_zone_id is set."
  type        = string
}

# --- Network ---

variable "vpc_cidr" {
  description = "CIDR block for the isolated call-server VPC. Defaults to 10.1.0.0/16 to avoid overlap with the 10.0.0.0/16 single-server default."
  type        = string
  default     = "10.1.0.0/16"
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
  description = "EC2 instance type. LiveKit is CPU-bound during transcoding; upsize for heavy workloads."
  type        = string
  default     = "t3.large"
}

variable "root_volume_size" {
  description = "Root volume size in GB"
  type        = number
  default     = 40
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

# --- DNS ---
# Certs are NOT provisioned at the AWS level for call-server — LiveKit terminates
# TLS in-process (typically via Caddy/Let's Encrypt configured by `ibl call start`).
# If hosted_zone_id is set, a Route53 A record is created pointing at the EIP.

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID. When set, creates an A record for base_domain → EIP. Leave empty to manage DNS externally."
  type        = string
  default     = ""
}

# --- LiveKit feature flags ---

variable "enable_sip" {
  description = "Open SIP signaling (5060 TCP+UDP, 5061 TLS) and SIP RTP media (10000-20000 UDP) in the security group. Defaults to false — enable only if LiveKit SIP is in use."
  type        = bool
  default     = false
}
