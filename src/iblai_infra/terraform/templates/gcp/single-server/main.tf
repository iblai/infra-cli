# ibl.ai Infrastructure - GCP Single Server
# Provisions: VPC, Compute Engine VM, global external Application Load Balancer,
# and optionally a Google-managed (or uploaded) SSL certificate + Cloud DNS.
#
# Storage note: unlike the AWS template, this stack provisions NO object
# storage. The ibl.ai platform continues to use AWS S3 - the operator supplies
# AWS credentials + bucket names at setup time (mirroring how the AWS VM reaches
# S3 today with static keys, no instance profile).

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  required_version = ">= 1.0"
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

# Latest Ubuntu 22.04 LTS image (used unless a custom image is supplied).
data "google_compute_image" "ubuntu" {
  count   = var.image == "" ? 1 : 0
  family  = "ubuntu-2204-lts"
  project = "ubuntu-os-cloud"
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  resource_prefix = "${var.project_name}-${var.environment}"
  network_tag     = "${var.project_name}-${var.environment}-server"

  boot_image = var.image != "" ? var.image : data.google_compute_image.ubuntu[0].self_link

  use_managed = var.certificate_method == "managed"
  use_upload  = var.certificate_method == "upload"
  use_https   = local.use_managed || local.use_upload

  # The full ibl.ai subdomain set (kept in sync with models.IBL_SUBDOMAINS).
  # The managed certificate and the A records both cover the base domain plus
  # every subdomain (one managed cert supports up to 100 domains).
  subdomains = [
    "learn.${var.base_domain}",
    "preview.learn.${var.base_domain}",
    "studio.learn.${var.base_domain}",
    "apps.learn.${var.base_domain}",
    "meilisearch.learn.${var.base_domain}",
    "api.data.${var.base_domain}",
    "api.${var.base_domain}",
    "asgi.data.${var.base_domain}",
    "llm.data.${var.base_domain}",
    "base.manager.${var.base_domain}",
    "auth.${var.base_domain}",
    "os.${var.base_domain}",
    "monitor.${var.base_domain}",
    "flowise.${var.base_domain}",
    "lms.${var.base_domain}",
    "platform.${var.base_domain}",
    "prometheus.${var.base_domain}",
  ]
  all_domains = concat([var.base_domain], local.subdomains)

  # Cloud DNS zone is only needed on the managed-cert path (A records must
  # resolve to the LB IP for Google to validate the certificate). When
  # create_dns_zone is set we create the zone here; otherwise we look up an
  # existing one by name.
  dns_zone_name = local.use_managed ? (
    var.create_dns_zone ? google_dns_managed_zone.main[0].name : data.google_dns_managed_zone.main[0].name
  ) : ""
}

# ---------------------------------------------------------------------------
# VPC & Networking
# ---------------------------------------------------------------------------

resource "google_compute_network" "main" {
  name                    = "${local.resource_prefix}-vpc"
  auto_create_subnetworks = false
}

# GCP subnets are regional (span every zone in the region), so one subnet
# replaces the AWS per-AZ subnet pair.
resource "google_compute_subnetwork" "main" {
  name          = "${local.resource_prefix}-subnet"
  ip_cidr_range = var.subnet_cidr
  region        = var.region
  network       = google_compute_network.main.id
}

# ---------------------------------------------------------------------------
# Firewall rules
# ---------------------------------------------------------------------------
#
# GCP has no per-instance security groups; firewall rules live on the network
# and target instances by network tag. Egress is allowed by default, so we only
# declare the two ingress rules we need. Public 80/443 is handled at Google's
# edge (the global LB frontend), NOT here - there is no in-VPC "ALB SG".

resource "google_compute_firewall" "ssh" {
  name          = "${local.resource_prefix}-allow-ssh"
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = ["${var.vpn_ip}/32"]
  target_tags   = [local.network_tag]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# Google Front End + health-check probe ranges. REQUIRED: without this the
# backend is unreachable/unhealthy behind the global external ALB and every
# request returns 502 ("no healthy upstream").
resource "google_compute_firewall" "lb_health" {
  name          = "${local.resource_prefix}-allow-lb-health"
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
  target_tags   = [local.network_tag]

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }
}

# ---------------------------------------------------------------------------
# Compute Engine instance
# ---------------------------------------------------------------------------
#
# SSH keys are injected via metadata (there is no key-pair resource). The boot
# disk is encrypted at rest by default with Google-managed keys. enable-oslogin
# is set FALSE so the metadata SSH key is honored on loosely-configured projects
# (an enforced org-level OS Login policy would still override this).

resource "google_compute_instance" "main" {
  name         = "${local.resource_prefix}-server"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = [local.network_tag]

  boot_disk {
    initialize_params {
      image = local.boot_image
      size  = var.volume_size
      type  = var.disk_type
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id

    # Ephemeral external IP for direct SSH (parity with the AWS public IP).
    access_config {}
  }

  metadata = merge(
    { "enable-oslogin" = "FALSE" },
    var.ssh_public_key != "" ? { "ssh-keys" = "${var.ssh_user}:${var.ssh_public_key}" } : {},
    var.skip_startup_script ? {} : { "startup-script" = file("${path.module}/startup-script.sh") },
  )

  service_account {
    # Minimal default: the app authenticates to AWS S3 with its own keys, so the
    # VM's service account needs no storage scope. cloud-platform is kept for
    # optional gcloud/ops use and logging.
    scopes = ["cloud-platform"]
  }

  # Allow in-place machine_type / metadata changes without recreating the disk.
  allow_stopping_for_update = true
}

# ---------------------------------------------------------------------------
# Backend: unmanaged instance group + health check + backend service
# ---------------------------------------------------------------------------
#
# A single stateful "pet" VM => unmanaged instance group (NOT a managed group,
# which would treat the VM as replaceable cattle and wipe its Docker/DB state).

resource "google_compute_instance_group" "main" {
  name      = "${local.resource_prefix}-ig"
  zone      = var.zone
  instances = [google_compute_instance.main.self_link]

  named_port {
    name = "http"
    port = 80
  }
}

# Health check probes the LMS heartbeat through nginx. GCP health checks only
# accept a literal 200 (no redirect-following, no 2xx-3xx matcher like AWS),
# and the platform's nginx catch-all answers unknown Hosts on "/" with a 301 -
# which would mark the single backend UNHEALTHY and serve 503 ("no healthy
# upstream") for everything. Probing with the learn.<domain> Host header routes
# to the LMS, whose /heartbeat returns a real 200. This also survives platform
# config re-saves (which regenerate the nginx catch-all), unlike any box-side
# nginx patch. Until the platform is installed (pre-setup), the backend is
# UNHEALTHY - expected, same as AWS.
resource "google_compute_health_check" "main" {
  name                = "${local.resource_prefix}-hc"
  check_interval_sec  = 30
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 2

  http_health_check {
    port         = 80
    host         = "learn.${var.base_domain}"
    request_path = "/heartbeat"
  }
}

resource "google_compute_backend_service" "main" {
  name                  = "${local.resource_prefix}-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTP"
  port_name             = "http"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.main.id]

  backend {
    group = google_compute_instance_group.main.self_link
  }
}

# ---------------------------------------------------------------------------
# Frontend: static IP + URL maps + proxies + forwarding rules
# ---------------------------------------------------------------------------

resource "google_compute_global_address" "main" {
  name = "${local.resource_prefix}-ip"
}

# HTTPS URL map: route everything to the backend (used only when certs exist).
resource "google_compute_url_map" "https" {
  count           = local.use_https ? 1 : 0
  name            = "${local.resource_prefix}-https-urlmap"
  default_service = google_compute_backend_service.main.id
}

# HTTP URL map (redirect variant): 301 to HTTPS when certs exist.
resource "google_compute_url_map" "http_redirect" {
  count = local.use_https ? 1 : 0
  name  = "${local.resource_prefix}-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

# HTTP URL map (forward variant): serve the backend directly when no certs.
resource "google_compute_url_map" "http_forward" {
  count           = local.use_https ? 0 : 1
  name            = "${local.resource_prefix}-http-forward"
  default_service = google_compute_backend_service.main.id
}

resource "google_compute_target_http_proxy" "main" {
  name    = "${local.resource_prefix}-http-proxy"
  url_map = local.use_https ? google_compute_url_map.http_redirect[0].id : google_compute_url_map.http_forward[0].id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.resource_prefix}-http-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.main.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.main.id
}

# ---------------------------------------------------------------------------
# TLS: SSL policy + certificate + HTTPS proxy + :443 forwarding rule
# ---------------------------------------------------------------------------

resource "google_compute_ssl_policy" "main" {
  count           = local.use_https ? 1 : 0
  name            = "${local.resource_prefix}-ssl-policy"
  profile         = "RESTRICTED"
  min_tls_version = "TLS_1_2"
}

# Google-managed certificate (certificate_method = "managed"). Provisions
# asynchronously: it goes ACTIVE only after every domain's A record resolves to
# the LB IP (see the DNS section) and DNS delegation is live. `terraform apply`
# returns before that, so HTTPS may take 10-60 minutes to come up.
resource "google_compute_managed_ssl_certificate" "main" {
  count = local.use_managed ? 1 : 0
  name  = "${local.resource_prefix}-cert"

  managed {
    domains = local.all_domains
  }
}

# Self-managed / uploaded certificate (certificate_method = "upload"). The
# certificate PEM must contain the full chain (leaf + intermediates).
resource "google_compute_ssl_certificate" "uploaded" {
  count       = local.use_upload ? 1 : 0
  name_prefix = "${local.resource_prefix}-cert-"
  certificate = file("${path.module}/${var.certificate_body_file}")
  private_key = file("${path.module}/${var.certificate_key_file}")

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_target_https_proxy" "main" {
  count      = local.use_https ? 1 : 0
  name       = "${local.resource_prefix}-https-proxy"
  url_map    = google_compute_url_map.https[0].id
  ssl_policy = google_compute_ssl_policy.main[0].id
  ssl_certificates = [
    local.use_managed ? google_compute_managed_ssl_certificate.main[0].id : google_compute_ssl_certificate.uploaded[0].id
  ]
}

resource "google_compute_global_forwarding_rule" "https" {
  count                 = local.use_https ? 1 : 0
  name                  = "${local.resource_prefix}-https-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.main.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.main[0].id
}

# ---------------------------------------------------------------------------
# Cloud DNS (managed-cert path only)
# ---------------------------------------------------------------------------
#
# Mirrors the AWS Route53 behaviour: DNS records are managed only when we own
# the certificate lifecycle (managed cert). For "upload"/"none" the operator
# manages DNS externally.

data "google_dns_managed_zone" "main" {
  count = local.use_managed && !var.create_dns_zone ? 1 : 0
  name  = var.dns_zone_name
}

resource "google_dns_managed_zone" "main" {
  count    = local.use_managed && var.create_dns_zone ? 1 : 0
  name     = var.dns_zone_name
  dns_name = "${var.base_domain}."
}

# A records: point every domain directly at the LB's static IP (no alias type
# needed, unlike the AWS ALB).
resource "google_dns_record_set" "app" {
  for_each = local.use_managed ? toset(local.all_domains) : toset([])

  name         = "${each.value}."
  type         = "A"
  ttl          = 300
  managed_zone = local.dns_zone_name
  rrdatas      = [google_compute_global_address.main.address]
}
