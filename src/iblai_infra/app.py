"""Main wizard orchestrator — wires all prompts into a single flow."""

from __future__ import annotations

from pathlib import Path

from iblai_infra import ui
from iblai_infra.models import CloudProvider, DeploymentType, InfraConfig
from iblai_infra.prompts.credentials import prompt_credentials
from iblai_infra.prompts.dns_certs import prompt_dns_and_certs, prompt_waf
from iblai_infra.prompts.infrastructure import prompt_project_and_compute, prompt_network_and_ssh
from iblai_infra.prompts.review import prompt_review
from iblai_infra.terraform.runner import TerraformRunner


def run_provision_wizard(show_banner: bool = True) -> None:
    """Run the full interactive provisioning wizard."""
    from iblai_infra.prompts.credentials import prompt_provider

    if show_banner:
        ui.banner()

    # Step 0 — choose the cloud, then collect a provider-specific config
    provider = prompt_provider()
    if provider == CloudProvider.GCP:
        config = _collect_gcp_config()
    else:
        config = _collect_aws_config()

    # Step 5 — Review & confirm
    prompt_review(config)

    # ----- Execute Terraform -----
    ui.newline()
    ui.console.print("  [brand]Provisioning infrastructure...[/brand]")

    runner = TerraformRunner(config)
    runner.setup()

    # Show workspace directory with files
    show_workspace(runner.ws)

    runner.init()
    add_count = runner.plan()

    if add_count == 0:
        ui.warning("No resources to create. Infrastructure may already exist.")
        return

    outputs = runner.apply()

    # ----- Show results -----
    show_results(config, outputs, runner.ws)

    # ----- Offer setup -----
    _offer_setup(config, runner.state)


def _collect_aws_config() -> InfraConfig:
    """Wizard steps 1-4 for AWS (single / multi / call-server)."""
    from iblai_infra.terraform.state import load_session, save_session

    # Step 1 — AWS credentials (reuse saved session if available)
    saved = load_session()
    if saved:
        credentials, _identity = saved
        ui.step_header(1, 5, "AWS Authentication")
        user = credentials.arn.split("/")[-1] if credentials.arn else "unknown"
        ui.success(f"Authenticated — [highlight]{user}[/highlight] ({credentials.account_id})")
    else:
        credentials = prompt_credentials()
        save_session(credentials)

    # Step 2 — Project & compute
    (
        project_name,
        environment,
        deployment_type,
        compute,
        multi_server,
        call_server,
    ) = prompt_project_and_compute()

    # Step 3 — Network & SSH (call-server uses 10.1/16 to avoid clashing with the
    # 10.0/16 default single-server and multi-server VPCs)
    default_cidr = "10.1.0.0/16" if deployment_type == DeploymentType.CALL else "10.0.0.0/16"
    network, ssh = prompt_network_and_ssh(
        credentials, project_name, environment, default_vpc_cidr=default_cidr
    )

    # Step 4 — Domain & certificates
    dns, certificates = prompt_dns_and_certs(
        credentials,
        is_call_server=(deployment_type == DeploymentType.CALL),
    )

    # Step 4b — WAF (single-server only)
    waf = None
    if deployment_type == DeploymentType.SINGLE:
        waf = prompt_waf(dns.base_domain)

    return InfraConfig(
        project_name=project_name,
        environment=environment,
        deployment_type=deployment_type,
        credentials=credentials,
        network=network,
        compute=compute,
        multi_server=multi_server,
        call_server=call_server,
        ssh=ssh,
        certificates=certificates,
        dns=dns,
        waf=waf,
    )


def _collect_gcp_config() -> InfraConfig:
    """Wizard steps 1-4 for GCP (single-server)."""
    from iblai_infra.prompts.credentials import prompt_gcp_credentials
    from iblai_infra.prompts.dns_certs import prompt_gcp_dns_and_certs
    from iblai_infra.prompts.infrastructure import prompt_gcp_project_and_compute

    # Step 1 — GCP credentials
    gcp_credentials = prompt_gcp_credentials()

    # Step 2 — Project & compute (single-server only)
    project_name, environment, compute = prompt_gcp_project_and_compute()

    # Step 3 — Network & SSH (no AWS key-pair option on GCP)
    network, ssh = prompt_network_and_ssh(
        credentials=None,
        project_name=project_name,
        environment=environment,
        default_vpc_cidr="10.0.0.0/16",
        allow_aws_keypair=False,
    )

    # Step 4 — Domain & certificates (Cloud DNS + Google-managed cert)
    dns, certificates = prompt_gcp_dns_and_certs(gcp_credentials)

    return InfraConfig(
        project_name=project_name,
        environment=environment,
        cloud=CloudProvider.GCP,
        deployment_type=DeploymentType.SINGLE,
        gcp_credentials=gcp_credentials,
        network=network,
        compute=compute,
        ssh=ssh,
        certificates=certificates,
        dns=dns,
    )


def show_workspace(ws: Path) -> None:
    """Show the user where Terraform files live."""
    ui.newline()

    files = sorted(ws.iterdir()) if ws.exists() else []
    if not files:
        return

    rows: list[tuple[str, str]] = []
    rows.append(("Directory", str(ws)))
    rows.append(("", ""))

    for f in files:
        if f.is_file():
            size = f.stat().st_size
            if size < 1024:
                size_str = f"{size} B"
            else:
                size_str = f"{size / 1024:.1f} KB"
            rows.append((f.name, f"[muted]{size_str}[/muted]"))

    ui.summary_panel("Terraform Workspace", rows)


def show_results(config: InfraConfig, outputs: dict, ws: Path) -> None:
    """Display the final infrastructure results."""
    rows: list[tuple[str, str]] = []

    if outputs.get("instance_public_ip"):
        rows.append(("Instance IP", outputs["instance_public_ip"]))
    if outputs.get("instance_private_ip"):
        rows.append(("Private IP", outputs["instance_private_ip"]))
    if outputs.get("alb_dns_name"):
        rows.append(("ALB DNS", outputs["alb_dns_name"]))

    if outputs.get("s3_bucket_backups"):
        rows.append(("S3 Backups", outputs["s3_bucket_backups"]))
    if outputs.get("s3_bucket_media"):
        rows.append(("S3 Media", outputs["s3_bucket_media"]))
    if outputs.get("s3_bucket_static"):
        rows.append(("S3 Static", outputs["s3_bucket_static"]))

    if outputs.get("ssh_command"):
        rows.append(("SSH", outputs["ssh_command"]))
    elif outputs.get("instance_public_ip"):
        key_flag = ""
        if config.ssh.private_key_path:
            key_flag = f"-i {config.ssh.private_key_path} "
        rows.append(("SSH", f"ssh {key_flag}ubuntu@{outputs['instance_public_ip']}"))

    if outputs.get("application_url"):
        rows.append(("App URL", outputs["application_url"]))

    ui.summary_panel("Infrastructure Ready", rows)

    # Show workspace location for reference
    ui.info(f"Workspace: [highlight]{ws}[/highlight]")
    ui.muted(f"  Contains: terraform.tfvars, main.tf, state.json, terraform.tfstate")
    if config.ssh.private_key_path:
        ui.info(f"SSH key:   [highlight]{config.ssh.private_key_path}[/highlight]")
    ui.newline()


def _offer_setup(config: InfraConfig, state) -> None:
    """After successful provision, offer to run platform setup."""
    import shutil

    import questionary

    from iblai_infra.models import ProjectState

    if not isinstance(state, ProjectState):
        return

    if not shutil.which("ansible-playbook"):
        ui.muted(
            "To bootstrap the VM with the IBL platform, install ansible-core and run "
            f"[brand]iblai infra setup {config.project_name}[/brand]"
        )
        ui.newline()
        return

    run_setup = questionary.confirm(
        "Run platform setup now? (Ansible will bootstrap the VM)",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    if not run_setup:
        ui.newline()
        ui.muted(
            f"Run [brand]iblai infra setup {config.project_name}[/brand] later to bootstrap the VM."
        )
        ui.newline()
        return

    from iblai_infra.ansible.runner import AnsibleRunner, CALL_ROLE_LABELS
    from iblai_infra.cli import _confirm_private_access_or_abort
    from iblai_infra.prompts.setup import prompt_setup

    # Same prerequisite gate as `iblai infra setup <name>` — bail before
    # any prompts collect input if the operator lacks access to the
    # private CLI ops / prod-images repos or ECR. Without this, the
    # post-provision shortcut (`provision` → "Run platform setup now?")
    # would silently skip the notice, since this path doesn't reach
    # `_run_setup_provisioned`.
    _confirm_private_access_or_abort()

    try:
        setup_config = prompt_setup(state)
    except KeyboardInterrupt:
        ui.newline()
        ui.muted(
            f"Setup interrupted. Run [brand]iblai infra setup {config.project_name}[/brand] to continue."
        )
        ui.newline()
        return

    # Call-server has its own (smaller) role set + dedicated playbook
    if config.deployment_type == DeploymentType.CALL:
        # Override env_config so ibl_call role runs `ibl config environment call-only`
        setup_config.env_config = "call-only"
        runner = AnsibleRunner(
            state, setup_config,
            playbook="call_playbook.yml",
            role_labels=CALL_ROLE_LABELS,
        )
    else:
        runner = AnsibleRunner(state, setup_config)

    if not runner.preflight():
        ui.newline()
        ui.muted(
            f"Fix the issue above, then run [brand]iblai infra setup {config.project_name}[/brand]"
        )
        ui.newline()
        return

    runner.setup()

    try:
        success = runner.run()
    except KeyboardInterrupt:
        from datetime import datetime, timezone
        from iblai_infra.terraform.state import save_state

        ui.newline()
        state.setup_status = "failed"
        state.updated_at = datetime.now(timezone.utc)
        save_state(state)
        ui.muted(
            f"Setup interrupted. Re-run [brand]iblai infra setup {config.project_name}[/brand]"
        )
        ui.newline()
        return

    if success:
        ui.newline()
        ip = setup_config.target_host
        key_flag = f"-i {setup_config.ssh_private_key_path} " if setup_config.ssh_private_key_path else ""
        ui.success(f"Platform bootstrapped on [highlight]{ip}[/highlight]")
        ui.info(f"SSH: [highlight]ssh {key_flag}ubuntu@{ip}[/highlight]")
        ui.newline()
