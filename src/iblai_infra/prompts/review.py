"""Step 5 — Review configuration and confirm."""

from __future__ import annotations

import questionary

from iblai_infra import ui
from iblai_infra.models import CertMethod, DeploymentType, InfraConfig, SSHKeyMethod

TOTAL_STEPS = 5


def prompt_review(config: InfraConfig) -> bool:
    """Display a full summary of the configuration and ask for confirmation."""

    ui.step_header(5, TOTAL_STEPS, "Review")

    # ----- Build summary rows -----
    rows: list[tuple[str, str]] = []

    # Project
    rows.append(("", "[bold]Project[/bold]"))
    rows.append(("Name", config.project_name))
    rows.append(("Environment", config.environment.value.capitalize()))
    rows.append(("Resource prefix", config.resource_prefix))

    # AWS
    rows.append(("", ""))
    rows.append(("", "[bold]AWS[/bold]"))
    rows.append(("Region", config.credentials.region))
    rows.append(("Account", config.credentials.account_id or "—"))
    rows.append(("Auth method", config.credentials.method.value))

    # Deployment type
    rows.append(("", ""))
    rows.append(("", "[bold]Deployment[/bold]"))
    rows.append(("Type", config.deployment_type.value.replace("-", " ").title()))

    # Compute
    rows.append(("", ""))
    rows.append(("", "[bold]Compute[/bold]"))
    if config.deployment_type == DeploymentType.MULTI and config.multi_server:
        ms = config.multi_server
        rows.append(("App servers", f"{ms.app_server_count} x {ms.app_server_instance_type}"))
        rows.append(("App volume", f"{ms.app_server_volume_size} GB gp3"))
        rows.append(("Services server", f"1 x {ms.services_instance_type}"))
        rows.append(("Services volume", f"{ms.services_volume_size} GB gp3"))
    else:
        rows.append(("Instance type", config.compute.instance_type))
        rows.append(("Volume", f"{config.compute.volume_size} GB {config.compute.volume_type}"))
    rows.append(("OS", "Ubuntu 22.04 LTS"))

    # Managed services (multi-server only)
    if config.deployment_type == DeploymentType.MULTI and config.multi_server:
        ms = config.multi_server
        rows.append(("", ""))
        rows.append(("", "[bold]Managed Services[/bold]"))
        rows.append(("MySQL (RDS)", "Enabled (Multi-AZ)" if ms.enable_mysql else "Disabled"))
        rows.append(("PostgreSQL (RDS)", "Enabled (Multi-AZ)" if ms.enable_postgres else "Disabled"))
        rows.append(("Redis (ElastiCache)", "Enabled (Multi-AZ)" if ms.enable_redis else "Disabled"))

    # Network
    rows.append(("", ""))
    rows.append(("", "[bold]Network[/bold]"))
    rows.append(("VPC CIDR", config.network.vpc_cidr))
    if config.deployment_type == DeploymentType.MULTI:
        rows.append(("Subnets", "Public + Private + DB + Cache (multi-AZ)"))
    else:
        rows.append(("Subnets", "2 public (multi-AZ)"))
    rows.append(("SSH access", f"{config.network.vpn_ip}/32 only"))
    if config.deployment_type == DeploymentType.MULTI and config.multi_server:
        rows.append(("Load balancer", f"ALB ({config.multi_server.app_server_count} targets)"))
    else:
        rows.append(("Load balancer", "Application LB (internet-facing)"))

    # SSH
    rows.append(("", ""))
    rows.append(("", "[bold]SSH Key[/bold]"))
    if config.ssh.method == SSHKeyMethod.GENERATE:
        rows.append(("Key", f"Generated ({config.ssh.key_name})"))
        if config.ssh.private_key_path:
            rows.append(("Private key", str(config.ssh.private_key_path)))
    elif config.ssh.method == SSHKeyMethod.EXISTING_FILE:
        rows.append(("Key", f"Provided ({config.ssh.key_name})"))
    else:
        rows.append(("Key", f"AWS key pair ({config.ssh.key_name})"))

    # DNS & Certificates
    rows.append(("", ""))
    rows.append(("", "[bold]Domain & Certificates[/bold]"))
    rows.append(("Domain", config.dns.base_domain))
    if config.certificates.method == CertMethod.ACM:
        rows.append(("DNS", "Route53 (auto-managed)"))
        rows.append(("Certificates", "ACM (auto-provisioned)"))
        rows.append(("Subdomains", f"{len(config.dns.subdomains)} records"))
    elif config.certificates.method == CertMethod.UPLOAD:
        rows.append(("DNS", "External (user-managed)"))
        rows.append(("Certificates", "Uploaded (ALB termination)"))
    else:
        rows.append(("DNS", "External (user-managed)"))
        rows.append(("Certificates", "None (HTTP only)"))

    # Storage
    rows.append(("", ""))
    rows.append(("", "[bold]Storage[/bold]"))
    rows.append(("S3 buckets", "3 (backups, media, static)"))

    ui.summary_panel("Infrastructure Summary", rows)

    # ----- Confirm -----
    proceed = questionary.confirm(
        "Proceed with infrastructure creation?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    if proceed is None or not proceed:
        ui.abort("Cancelled — no infrastructure was created.")

    return True
