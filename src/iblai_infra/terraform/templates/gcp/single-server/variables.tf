# ibl.ai Infrastructure - GCP Single Server Variables

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone (must be within region). The VM and its instance group are zonal."
  type        = string
  default     = "us-central1-a"
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

# --- Network ---

variable "subnet_cidr" {
  description = "Primary IP range for the regional subnet"
  type        = string
  default     = "10.0.0.0/16"
}

variable "vpn_ip" {
  description = "IP address allowed SSH access (port 22)"
  type        = string
}

# --- Compute ---

variable "image" {
  description = "Custom boot image (self_link or family path). When set, uses this instead of the default Ubuntu 22.04 lookup."
  type        = string
  default     = ""
}

variable "skip_startup_script" {
  description = "Skip the startup-script bootstrap (set true for pre-built images)"
  type        = bool
  default     = false
}

variable "machine_type" {
  description = "Compute Engine machine type"
  type        = string
  default     = "e2-standard-8"
}

variable "volume_size" {
  description = "Boot disk size in GB"
  type        = number
  default     = 100
}

variable "disk_type" {
  description = "Boot disk type (pd-balanced, pd-ssd, pd-standard)"
  type        = string
  default     = "pd-balanced"
}

# --- SSH ---

variable "ssh_user" {
  description = "Login user created from the SSH key metadata"
  type        = string
  default     = "ubuntu"
}

variable "ssh_public_key" {
  description = "SSH public key material injected via instance metadata"
  type        = string
  default     = ""
}

# --- Certificates ---

variable "certificate_method" {
  description = "Certificate method: managed, upload, or none"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["managed", "upload", "none"], var.certificate_method)
    error_message = "certificate_method must be 'managed', 'upload', or 'none'"
  }
}

variable "certificate_body_file" {
  description = "Path to certificate PEM file, full chain (when certificate_method = upload)"
  type        = string
  default     = ""
}

variable "certificate_key_file" {
  description = "Path to certificate private key PEM file (when certificate_method = upload)"
  type        = string
  default     = ""
}

# --- DNS ---

variable "dns_zone_name" {
  description = "Cloud DNS managed zone name (required when certificate_method = managed)"
  type        = string
  default     = ""
}

variable "create_dns_zone" {
  description = "Create the Cloud DNS managed zone (true) or use an existing one (false). When true, delegate the printed nameservers at your registrar."
  type        = bool
  default     = false
}
