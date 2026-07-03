# ibl.ai Infrastructure - GCP Single Server Outputs

output "network_id" {
  description = "VPC network ID"
  value       = google_compute_network.main.id
}

output "subnet_id" {
  description = "Regional subnet ID"
  value       = google_compute_subnetwork.main.id
}

output "instance_id" {
  description = "Compute Engine instance ID"
  value       = google_compute_instance.main.instance_id
}

# NOTE: the name `instance_public_ip` is a contract with the setup flow
# (`iblai infra setup <name>` reads state.outputs["instance_public_ip"]).
output "instance_public_ip" {
  description = "VM external IP"
  value       = google_compute_instance.main.network_interface[0].access_config[0].nat_ip
}

output "instance_private_ip" {
  description = "VM internal IP"
  value       = google_compute_instance.main.network_interface[0].network_ip
}

output "lb_ip_address" {
  description = "Global load balancer IP (point DNS A records here)"
  value       = google_compute_global_address.main.address
}

output "certificate_name" {
  description = "SSL certificate name (empty when certificate_method = none)"
  value = local.use_managed ? google_compute_managed_ssl_certificate.main[0].name : (
    local.use_upload ? google_compute_ssl_certificate.uploaded[0].name : ""
  )
}

# Nameservers to delegate at the registrar. Populated only when this stack
# created the Cloud DNS zone (create_dns_zone = true); empty otherwise.
output "dns_name_servers" {
  description = "Cloud DNS nameservers to set at your registrar (only when the zone was created here)"
  value       = local.use_managed && var.create_dns_zone ? google_dns_managed_zone.main[0].name_servers : []
}

output "ssh_command" {
  description = "SSH connection command"
  value       = "ssh ${var.ssh_user}@${google_compute_instance.main.network_interface[0].access_config[0].nat_ip}"
}

output "application_url" {
  description = "Primary application URL"
  value = var.certificate_method != "none" ? (
    var.certificate_method == "managed" ? "https://learn.${var.base_domain}" : "https://${google_compute_global_address.main.address}"
  ) : "http://${google_compute_global_address.main.address}"
}
