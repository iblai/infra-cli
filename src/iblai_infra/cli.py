"""CLI entry point — `iblai infra <command>` structure.

Root:  iblai --version | --help
Group: iblai infra provision | retry | setup | destroy | status | list
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from iblai_infra import __version__, ui
from iblai_infra.terraform.state import list_all_states, load_session, load_state, save_session, save_state

# ---------------------------------------------------------------------------
# Root app: `iblai`
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="iblai",
    help="ibl.ai CLI — Infrastructure, deployment, and platform management.",
    no_args_is_help=True,
    add_completion=False,
)


def version_callback(value: bool) -> None:
    if value:
        ui.console.print(f"[brand]iblai[/brand] v{__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """ibl.ai CLI — Infrastructure, deployment, and platform management."""


# ---------------------------------------------------------------------------
# Subcommand group: `iblai infra`
# ---------------------------------------------------------------------------

infra_app = typer.Typer(
    name="infra",
    help="Infrastructure provisioning and management for AWS.",
    invoke_without_command=True,
)

app.add_typer(infra_app)


@infra_app.callback(invoke_without_command=True)
def infra_root(ctx: typer.Context) -> None:
    """Infrastructure provisioning and management for AWS."""
    if ctx.invoked_subcommand is not None:
        return

    ui.banner()

    table = Table(
        show_header=False,
        box=None,
        padding=(0, 2),
        expand=False,
    )
    table.add_column("Command", style=f"bold {ui.IBL_BLUE_LIGHT}", min_width=36)
    table.add_column("Description", style="white")

    commands = [
        ("iblai infra provision", "Launch the interactive provisioning wizard"),
        ("iblai infra retry <name>", "Retry a failed provisioning run"),
        ("iblai infra setup", "Set up the IBL platform on a server"),
        ("iblai infra resetup <name>", "Re-setup with new domain and secrets"),
        ("iblai infra launch --ami-id ...", "Launch from AMI (non-interactive, CI/CD)"),
        ("iblai infra destroy <name>", "Destroy existing infrastructure"),
        ("iblai infra status <name>", "Show infrastructure details and outputs"),
        ("iblai infra list", "List all managed environments"),
        ("iblai infra permissions", "Show required IAM policy"),
        ("iblai infra permissions --check", "Verify your AWS permissions"),
        ("iblai infra auth", "Authenticate or switch AWS credentials"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)

    ui.section("Available Commands", table)

    import questionary

    ui.newline()
    action = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Provision infrastructure", value="provision"),
            questionary.Choice("Retry failed provisioning", value="retry"),
            questionary.Choice("Set up platform on a server", value="setup"),
            questionary.Choice("Re-setup an existing environment", value="resetup"),
            questionary.Choice("Check AWS permissions", value="permissions"),
            questionary.Choice("List managed environments", value="list"),
            questionary.Choice("Show required IAM policy", value="policy"),
            questionary.Choice("Switch AWS credentials", value="auth"),
            questionary.Choice("Exit", value="exit"),
        ],
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    if action is None or action == "exit":
        ui.newline()
        raise typer.Exit()

    ui.newline()

    if action == "provision":
        from iblai_infra.app import run_provision_wizard
        try:
            run_provision_wizard(show_banner=False)
        except KeyboardInterrupt:
            ui.newline()
            ui.abort("Interrupted.")
    elif action == "retry":
        _interactive_retry()
    elif action == "setup":
        _interactive_setup()
    elif action == "resetup":
        _interactive_resetup()
    elif action == "permissions":
        ctx.invoke(permissions, check=True, profile=None, region="us-east-1")
    elif action == "list":
        ctx.invoke(list_cmd)
    elif action == "policy":
        ctx.invoke(permissions, check=False, profile=None, region="us-east-1")
    elif action == "auth":
        ctx.invoke(auth)

    ui.newline()


@infra_app.command()
def auth() -> None:
    """Authenticate or switch AWS credentials."""
    from iblai_infra.prompts.credentials import prompt_credentials
    from iblai_infra.terraform.state import clear_session

    clear_session()
    prompt_credentials(show_step=False)
    ui.newline()


@infra_app.command()
def provision() -> None:
    """Launch the interactive provisioning wizard."""
    from iblai_infra.app import run_provision_wizard

    try:
        run_provision_wizard()
    except KeyboardInterrupt:
        ui.newline()
        ui.abort("Interrupted.")


@infra_app.command()
def retry(
    name: str = typer.Argument(help="Project name to retry"),
) -> None:
    """Retry a failed provisioning run."""
    _run_retry(name)


def _interactive_retry() -> None:
    """Launch retry from the landing menu — prompts for project name."""
    import questionary

    states = list_all_states()
    eligible = [s for s in states if s.status == "failed"]

    if not eligible:
        ui.info("No failed environments found to retry.")
        ui.muted("Run [brand]iblai infra provision[/brand] to create a new environment.")
        ui.newline()
        return

    if len(eligible) == 1:
        _run_retry(eligible[0].name)
        return

    choices = [
        questionary.Choice(
            f"{s.name} ({s.config.dns.base_domain})",
            value=s.name,
        )
        for s in eligible
    ]
    name = questionary.select(
        "Which environment to retry?",
        choices=choices,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if name is None:
        return

    _run_retry(name)


def _run_retry(name: str) -> None:
    """Retry a failed Terraform provisioning run using the existing workspace."""
    from pathlib import Path

    from iblai_infra.terraform.runner import TerraformRunner
    from iblai_infra.terraform.state import save_state as _save_state

    state = load_state(name)
    if state is None:
        ui.error(f"No infrastructure found with name: {name}")
        raise typer.Exit(1)

    if state.status == "created":
        ui.info(f"Infrastructure '{name}' is already provisioned (status: created).")
        ui.muted(f"Run [brand]iblai infra setup {name}[/brand] to bootstrap the platform.")
        raise typer.Exit(0)

    if state.status == "destroyed":
        ui.error(f"Infrastructure '{name}' has been destroyed. Run a new provision instead.")
        raise typer.Exit(1)

    if state.status not in ("failed", "initialized"):
        ui.error(f"Infrastructure '{name}' has status '{state.status}'. Cannot retry.")
        raise typer.Exit(1)

    ws = Path(state.workspace_path)
    if not ws.exists() or not (ws / "main.tf").exists():
        ui.error(f"Workspace not found at {ws}. Run a new provision instead.")
        raise typer.Exit(1)

    ui.banner()
    ui.info(f"Retrying provisioning for [highlight]{name}[/highlight]")
    ui.info(f"Domain: [highlight]{state.config.dns.base_domain}[/highlight]")
    ui.info(f"Workspace: [highlight]{ws}[/highlight]")
    ui.newline()

    # Clean up conflicting CNAME records if using Route53
    if state.config.dns.use_route53 and state.config.dns.hosted_zone_id:
        from iblai_infra.models import IBL_SUBDOMAINS
        from iblai_infra.providers.aws import (
            delete_route53_records,
            find_conflicting_records,
            get_session,
        )
        from rich.status import Status

        session = get_session(state.config.credentials)
        subdomains = [s.format(domain=state.config.dns.base_domain) for s in IBL_SUBDOMAINS]

        with Status("  [info]Checking for conflicting DNS records...[/info]", console=ui.console):
            conflicts = find_conflicting_records(
                session, state.config.dns.hosted_zone_id, subdomains,
            )

        if conflicts:
            import questionary

            ui.warning(
                f"Found {len(conflicts)} existing CNAME record(s) blocking A record creation:"
            )
            for c in conflicts:
                values = [rr["Value"] for rr in c.get("ResourceRecords", [])]
                ui.muted(f"  CNAME  {c['Name'].rstrip('.')}  →  {', '.join(values)}")

            ui.newline()
            delete_confirm = questionary.confirm(
                "Delete these conflicting CNAME records to proceed?",
                default=True,
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if not delete_confirm:
                ui.abort("Retry cancelled — no DNS changes made.")

            with Status("  [info]Removing conflicting CNAME records...[/info]", console=ui.console):
                delete_route53_records(
                    session, state.config.dns.hosted_zone_id, conflicts,
                )
            ui.success(f"Removed {len(conflicts)} conflicting CNAME record(s)")
        else:
            ui.success("No conflicting DNS records found")

        ui.newline()

    # Reuse the existing workspace — re-copy .tf templates to pick up any fixes,
    # but preserve the existing terraform.tfvars (avoids re-generating bucket names).
    runner = TerraformRunner(state.config)
    runner.ws = ws
    runner.state = state
    runner._copy_templates()
    ui.success(f"Templates updated  [muted]{ws}[/muted]")

    runner.init()
    add_count = runner.plan()

    if add_count == 0:
        ui.info("No changes needed. All resources are up to date.")
        state.status = "created"
        _save_state(state)
        ui.success(f"Infrastructure '{name}' is now in created state.")
        return

    outputs = runner.apply()

    from iblai_infra.app import _show_results, _offer_setup
    _show_results(state.config, outputs, ws)
    _offer_setup(state.config, runner.state)


@infra_app.command()
def setup(
    name: str = typer.Argument(None, help="Project name (from provision). Omit to set up an existing server."),
) -> None:
    """Set up the IBL platform on a server."""
    if name:
        _run_setup_provisioned(name)
    else:
        _run_setup_interactive()


@infra_app.command()
def resetup(
    name: str = typer.Argument(help="Project name to re-setup"),
) -> None:
    """Re-setup an existing environment with a new domain and fresh secrets."""
    _run_resetup(name)


@infra_app.command()
def launch(
    ami_id: str = typer.Option(..., "--ami-id", help="Custom AMI ID to launch from"),
    domain: str = typer.Option(..., "--domain", help="Base domain (e.g. ami.iblai.org)"),
    hosted_zone_id: str = typer.Option(..., "--hosted-zone-id", help="Route53 hosted zone ID"),
    aws_key_id: str = typer.Option(..., "--aws-key-id", help="AWS access key ID"),
    aws_secret_key: str = typer.Option(..., "--aws-secret-key", help="AWS secret access key"),
    ssh_public_key: str = typer.Option(..., "--ssh-public-key", help="SSH public key material"),
    ssh_key: Path = typer.Option(..., "--ssh-key", help="Path to SSH private key"),
    git_token: str = typer.Option(..., "--git-token", help="GitHub Personal Access Token"),
    admin_email: str = typer.Option(..., "--admin-email", help="Admin email address"),
    admin_password: str = typer.Option(..., "--admin-password", help="Admin password (min 8 chars)"),
    vpn_ip: str = typer.Option(..., "--vpn-ip", help="IP address allowed SSH access"),
    name: str | None = typer.Option(None, "--name", help="Project name (auto-generated from domain if omitted)"),
    ssh_user: str = typer.Option("ubuntu", "--ssh-user", help="SSH user"),
    aws_region: str = typer.Option("us-east-1", "--aws-region", help="AWS region"),
    instance_type: str = typer.Option("t3.2xlarge", "--instance-type", help="EC2 instance type"),
    volume_size: int = typer.Option(200, "--volume-size", help="Root volume size in GB"),
    environment: str = typer.Option("staging", "--environment", help="Environment (dev, staging, prod)"),
    cli_tag: str = typer.Option("3.19.0", "--cli-tag", help="iblai-cli-ops release tag"),
    admin_username: str = typer.Option("ibl_admin", "--admin-username", help="Admin username"),
    openai_key: str = typer.Option("", "--openai-key", help="OpenAI API key (optional)"),
    enable_ai: bool = typer.Option(True, "--enable-ai/--no-ai", help="Enable AI features"),
) -> None:
    """Launch IBL platform from a pre-built AMI. Non-interactive, CI/CD-friendly.

    Provisions AWS infrastructure (VPC, ALB, ACM certs, Route53, EC2) via Terraform,
    then configures the platform (domain, secrets, service restarts) via Ansible.
    """
    _run_launch(
        ami_id=ami_id, domain=domain, hosted_zone_id=hosted_zone_id,
        aws_key_id=aws_key_id, aws_secret_key=aws_secret_key,
        ssh_public_key=ssh_public_key, ssh_key=ssh_key,
        git_token=git_token, admin_email=admin_email,
        admin_password=admin_password, vpn_ip=vpn_ip, name=name,
        ssh_user=ssh_user, aws_region=aws_region,
        instance_type=instance_type, volume_size=volume_size,
        environment=environment, cli_tag=cli_tag,
        admin_username=admin_username, openai_key=openai_key,
        enable_ai=enable_ai,
    )


def _run_launch(
    *,
    ami_id: str,
    domain: str,
    hosted_zone_id: str,
    aws_key_id: str,
    aws_secret_key: str,
    ssh_public_key: str,
    ssh_key: Path,
    git_token: str,
    admin_email: str,
    admin_password: str,
    vpn_ip: str,
    name: str | None,
    ssh_user: str,
    aws_region: str,
    instance_type: str,
    volume_size: int,
    environment: str,
    cli_tag: str,
    admin_username: str,
    openai_key: str,
    enable_ai: bool,
) -> None:
    """Provision infrastructure from AMI and configure platform. Non-interactive."""
    import os
    import shutil
    from datetime import datetime, timezone

    from iblai_infra.ansible.runner import AnsibleRunner, LAUNCH_ROLE_LABELS
    from iblai_infra.models import (
        AWSCredentials,
        AuthMethod,
        CertificateConfig,
        CertMethod,
        ComputeConfig,
        DNSConfig,
        Environment,
        InfraConfig,
        NetworkConfig,
        ProjectState,
        SetupConfig,
        SSHConfig,
        SSHKeyMethod,
    )
    from iblai_infra.terraform.runner import TerraformRunner
    from iblai_infra.terraform.state import WORKSPACE_ROOT

    # Derive project name
    project_name = name or domain.replace(".", "-")
    if len(project_name) > 32:
        project_name = project_name[:32]

    # Validate SSH key
    ssh_key = Path(ssh_key).expanduser()
    if not ssh_key.exists():
        ui.error(f"SSH key not found: {ssh_key}")
        raise typer.Exit(1)
    mode = ssh_key.stat().st_mode & 0o777
    if mode > 0o600:
        os.chmod(ssh_key, 0o600)

    # Map environment string
    env_map = {"dev": Environment.DEV, "staging": Environment.STAGING, "prod": Environment.PROD}
    env = env_map.get(environment, Environment.STAGING)

    # Check prerequisites
    if shutil.which("terraform") is None:
        ui.error("terraform not found. Install from https://www.terraform.io/downloads")
        raise typer.Exit(1)
    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found. Install with: pip install ansible-core")
        raise typer.Exit(1)

    # Build InfraConfig
    infra_config = InfraConfig(
        project_name=project_name,
        environment=env,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY,
            access_key_id=aws_key_id,
            secret_access_key=aws_secret_key,
            region=aws_region,
        ),
        network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip=vpn_ip),
        compute=ComputeConfig(
            instance_type=instance_type,
            volume_size=volume_size,
            ami_id=ami_id,
        ),
        ssh=SSHConfig(
            method=SSHKeyMethod.GENERATE,
            key_name=f"{project_name}-{environment}",
            public_key=ssh_public_key,
            private_key_path=ssh_key,
        ),
        certificates=CertificateConfig(
            method=CertMethod.ACM,
            hosted_zone_id=hosted_zone_id,
        ),
        dns=DNSConfig(
            base_domain=domain,
            use_route53=True,
            hosted_zone_id=hosted_zone_id,
        ),
    )

    ui.info(f"Launching platform from AMI [highlight]{ami_id}[/highlight]")
    ui.info(f"Domain: [highlight]{domain}[/highlight]")
    ui.info(f"Project: [highlight]{project_name}[/highlight]")
    ui.newline()

    # ---- Phase 1: Terraform ----
    ui.info("Phase 1: Provisioning infrastructure...")
    ui.newline()

    tf_runner = TerraformRunner(infra_config)
    tf_runner.setup()
    tf_runner.init()
    count = tf_runner.plan()
    if count == 0:
        ui.warning("No resources to create.")
    outputs = tf_runner.apply()

    state = tf_runner.state
    state.provider = "launch"

    instance_ip = outputs.get("instance_public_ip", "")
    if not instance_ip:
        ui.error("No instance IP found in Terraform outputs.")
        raise typer.Exit(1)

    ui.newline()
    ui.success(f"Infrastructure provisioned. Instance IP: [highlight]{instance_ip}[/highlight]")
    ui.newline()

    # ---- Phase 2: Ansible ----
    ui.info("Phase 2: Configuring platform...")
    ui.newline()

    setup_config = SetupConfig(
        ssh_private_key_path=ssh_key,
        ssh_user=ssh_user,
        target_host=instance_ip,
        base_domain=domain,
        cli_ops_release_tag=cli_tag,
        enable_ai=enable_ai,
        aws_access_key_id=aws_key_id,
        aws_secret_access_key=aws_secret_key,
        aws_default_region=aws_region,
        git_access_token=git_token,
        openai_api_key=openai_key,
        admin_username=admin_username,
        admin_email=admin_email,
        admin_password=admin_password,
    )

    ansible_runner = AnsibleRunner(
        state, setup_config,
        playbook="launch_playbook.yml",
        role_labels=LAUNCH_ROLE_LABELS,
    )

    if not ansible_runner.preflight():
        raise typer.Exit(1)

    ansible_runner.setup()

    try:
        success = ansible_runner.run()
    except KeyboardInterrupt:
        ui.newline()
        state.setup_status = "failed"
        state.updated_at = datetime.now(timezone.utc)
        save_state(state)
        ui.abort("Interrupted.")

    if success:
        ui.newline()
        app_url = outputs.get("application_url", f"https://{domain}")
        ui.success(f"Platform launched successfully!")
        ui.info(f"URL: [highlight]{app_url}[/highlight]")
        ui.info(f"SSH: [highlight]ssh -i {ssh_key} {ssh_user}@{instance_ip}[/highlight]")
        ui.newline()
        ui.muted(f"To destroy: [brand]iblai infra destroy {project_name}[/brand]")
        ui.newline()
    else:
        raise typer.Exit(1)


def _interactive_setup() -> None:
    """Launch setup from the landing menu."""
    import questionary

    states = list_all_states()
    eligible = [s for s in states if s.status == "created"]

    # Ask which path
    choices = []
    if eligible:
        choices.append(questionary.Choice(
            "Set up a provisioned environment", value="provisioned",
        ))
    choices.append(questionary.Choice(
        "Set up an existing server (no Terraform)", value="existing",
    ))

    if len(choices) == 1:
        # Only "existing server" is available
        _run_setup_interactive()
        return

    path = questionary.select(
        "What would you like to set up?",
        choices=choices,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if path is None:
        return

    if path == "existing":
        _run_setup_interactive()
        return

    # Provisioned path — select environment
    if len(eligible) == 1:
        _run_setup_provisioned(eligible[0].name)
        return

    env_choices = [
        questionary.Choice(
            f"{s.name} ({s.config.dns.base_domain})",
            value=s.name,
        )
        for s in eligible
    ]
    selected = questionary.select(
        "Which environment?",
        choices=env_choices,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if selected is None:
        return

    _run_setup_provisioned(selected)


def _interactive_resetup() -> None:
    """Launch resetup from the landing menu."""
    import questionary

    states = list_all_states()
    eligible = [s for s in states if s.status == "created"]

    if not eligible:
        ui.info("No environments available for re-setup.")
        ui.muted("Run [brand]iblai infra provision[/brand] or [brand]iblai infra setup[/brand] first.")
        return

    if len(eligible) == 1:
        _run_resetup(eligible[0].name)
        return

    env_choices = [
        questionary.Choice(
            f"{s.name} ({s.config.dns.base_domain})",
            value=s.name,
        )
        for s in eligible
    ]
    selected = questionary.select(
        "Which environment to re-setup?",
        choices=env_choices,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if selected is None:
        return

    _run_resetup(selected)


def _run_resetup(name: str) -> None:
    """Re-setup an existing environment with a new domain and fresh secrets."""
    state = load_state(name)
    if state is None:
        ui.error(f"No infrastructure found with name: {name}")
        raise typer.Exit(1)

    if state.status != "created":
        ui.error(
            f"Infrastructure '{name}' has status '{state.status}'. "
            "It must be fully provisioned (status: created) before re-setup."
        )
        raise typer.Exit(1)

    if not state.outputs or not state.outputs.get("instance_public_ip"):
        ui.error("No instance IP found in Terraform outputs. Re-run provisioning.")
        raise typer.Exit(1)

    import shutil

    from iblai_infra.prompts.setup import prompt_resetup

    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found")
        ui.newline()
        ui.info("Install with: [highlight]pip install ansible-core[/highlight]")
        ui.muted(f"Then re-run: [brand]iblai infra resetup {name}[/brand]")
        ui.newline()
        raise typer.Exit(1)

    try:
        setup_config = prompt_resetup(state)
    except KeyboardInterrupt:
        ui.newline()
        ui.abort("Interrupted.")

    _confirm_and_run(state, setup_config, f"iblai infra resetup {name}")


def _run_setup_provisioned(name: str) -> None:
    """Set up a Terraform-provisioned environment by name."""
    state = load_state(name)
    if state is None:
        ui.error(f"No infrastructure found with name: {name}")
        raise typer.Exit(1)

    if state.status == "destroyed":
        ui.error(f"Infrastructure '{name}' has been destroyed. Provision it first.")
        raise typer.Exit(1)

    if state.status != "created":
        ui.error(
            f"Infrastructure '{name}' has status '{state.status}'. "
            "It must be fully provisioned (status: created) before setup."
        )
        raise typer.Exit(1)

    if not state.outputs or not state.outputs.get("instance_public_ip"):
        ui.error("No instance IP found in Terraform outputs. Re-run provisioning.")
        raise typer.Exit(1)

    # Check if already set up
    if state.setup_status == "completed":
        import questionary

        ui.warning(f"Platform setup already completed for '{name}'.")
        rerun = questionary.confirm(
            "Re-run setup?",
            default=False,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if not rerun:
            raise typer.Exit(0)

    import shutil

    from iblai_infra.ansible.runner import AnsibleRunner
    from iblai_infra.prompts.setup import prompt_setup

    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found")
        ui.newline()
        ui.info("Install with: [highlight]pip install ansible-core[/highlight]")
        ui.muted(f"Then re-run: [brand]iblai infra setup {name}[/brand]")
        ui.newline()
        raise typer.Exit(1)

    try:
        setup_config = prompt_setup(state)
    except KeyboardInterrupt:
        ui.newline()
        ui.abort("Interrupted.")

    _confirm_and_run(state, setup_config, f"iblai infra setup {name}")


def _run_setup_interactive() -> None:
    """Set up an existing server — no Terraform state required."""
    import shutil
    from datetime import datetime, timezone

    from iblai_infra.models import (
        AWSCredentials,
        AuthMethod,
        CertificateConfig,
        CertMethod,
        ComputeConfig,
        DNSConfig,
        Environment,
        InfraConfig,
        NetworkConfig,
        ProjectState,
        SSHConfig,
        SSHKeyMethod,
    )
    from iblai_infra.prompts.setup import prompt_bootstrap
    from iblai_infra.terraform.state import WORKSPACE_ROOT

    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found")
        ui.newline()
        ui.info("Install with: [highlight]pip install ansible-core[/highlight]")
        ui.muted("Then re-run: [brand]iblai infra setup[/brand]")
        ui.newline()
        raise typer.Exit(1)

    try:
        setup_config, meta = prompt_bootstrap()
    except KeyboardInterrupt:
        ui.newline()
        ui.abort("Interrupted.")

    project_name = meta["project_name"]

    # Check if project name already exists
    existing = load_state(project_name)
    if existing is not None:
        if existing.setup_status == "completed":
            import questionary

            ui.warning(f"Project '{project_name}' already exists with completed setup.")
            rerun = questionary.confirm(
                "Re-run setup?",
                default=False,
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if not rerun:
                raise typer.Exit(0)
            state = existing
        elif existing.status == "created":
            ui.info(f"Resuming setup for existing project '{project_name}'.")
            state = existing
        else:
            ui.error(
                f"Project '{project_name}' already exists with status '{existing.status}'."
            )
            ui.muted("Choose a different project name or destroy the existing one.")
            raise typer.Exit(1)
    else:
        workspace_path = str(WORKSPACE_ROOT / f"{project_name}-bootstrap")
        state = ProjectState(
            name=project_name,
            provider="bootstrap",
            status="created",
            config=InfraConfig(
                project_name=project_name,
                environment=Environment.DEV,
                credentials=AWSCredentials(
                    method=AuthMethod.ACCESS_KEY,
                    access_key_id=setup_config.aws_access_key_id,
                    secret_access_key=setup_config.aws_secret_access_key,
                    region=setup_config.aws_default_region,
                ),
                network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="0.0.0.0"),
                compute=ComputeConfig(),
                ssh=SSHConfig(
                    method=SSHKeyMethod.EXISTING_FILE,
                    key_name="bootstrap",
                    private_key_path=setup_config.ssh_private_key_path,
                ),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain=setup_config.base_domain),
            ),
            outputs={"instance_public_ip": setup_config.target_host},
            workspace_path=workspace_path,
        )
        save_state(state)

    _confirm_and_run(state, setup_config, "iblai infra setup")


def _confirm_and_run(state, setup_config, rerun_hint: str) -> None:
    """Show summary, confirm, and run Ansible. Shared by both setup paths."""
    from datetime import datetime, timezone

    from iblai_infra.ansible.runner import AnsibleRunner

    rows = []
    if setup_config.is_resetup:
        rows.append(("Mode", "Re-setup"))
    rows.extend([
        ("Target", setup_config.target_host),
        ("SSH key", str(setup_config.ssh_private_key_path)),
        ("Domain", setup_config.base_domain),
        ("CLI ops tag", setup_config.cli_ops_release_tag),
        ("edX version", setup_config.edx_version),
        ("Env config", setup_config.env_config),
        ("AWS region", setup_config.aws_default_region),
    ])
    ui.summary_panel("Setup Summary", rows)

    import questionary

    confirm = questionary.confirm(
        "Proceed with setup?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if not confirm:
        ui.abort("Cancelled.")

    # Update state with new base domain so `iblai infra list` reflects it
    if setup_config.is_resetup and state.config.dns.base_domain != setup_config.base_domain:
        state.config.dns.base_domain = setup_config.base_domain
        save_state(state)

    runner = AnsibleRunner(state, setup_config)

    if not runner.preflight():
        raise typer.Exit(1)

    runner.setup()

    try:
        success = runner.run()
    except KeyboardInterrupt:
        ui.newline()
        state.setup_status = "failed"
        state.updated_at = datetime.now(timezone.utc)
        save_state(state)
        ui.abort(f"Interrupted. Re-run with: {rerun_hint}")

    if success:
        ui.newline()
        ip = setup_config.target_host
        key_flag = f"-i {setup_config.ssh_private_key_path} " if setup_config.ssh_private_key_path else ""
        ui.success(f"Platform setup complete on [highlight]{ip}[/highlight]")
        ui.info(f"SSH: [highlight]ssh {key_flag}{setup_config.ssh_user}@{ip}[/highlight]")
        ui.newline()


@infra_app.command()
def destroy(
    name: str = typer.Argument(help="Project name to destroy"),
) -> None:
    """Destroy existing infrastructure."""
    import questionary

    state = load_state(name)
    if state is None:
        ui.error(f"No infrastructure found with name: {name}")
        raise typer.Exit(1)

    if state.status == "destroyed":
        ui.warning(f"Infrastructure '{name}' is already destroyed.")
        raise typer.Exit(0)

    # Bootstrap and launch projects — destroy via Terraform if launch, otherwise just mark
    if state.provider == "bootstrap":
        confirm_remove = questionary.confirm(
            f"Remove bootstrap project '{name}' from tracked environments?",
            default=False,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if not confirm_remove:
            ui.abort("Cancelled.")
        state.status = "destroyed"
        state.outputs = None
        save_state(state)
        ui.success(f"Bootstrap project '{name}' marked as destroyed.")
        return

    ui.banner()
    ui.warning(
        f"This will permanently destroy ALL infrastructure for: [highlight]{name}[/highlight]"
    )

    if state.outputs:
        rows = []
        for k, v in state.outputs.items():
            if isinstance(v, str) and v:
                rows.append((k, v))
        if rows:
            ui.summary_panel("Resources to Destroy", rows[:10])

    confirm = questionary.confirm(
        "Are you sure you want to destroy this infrastructure?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    if not confirm:
        ui.abort("Cancelled.")

    # Double confirm for production
    if state.config.environment.value == "prod":
        confirm2 = questionary.text(
            f'Type "{name}" to confirm production destruction:',
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if confirm2 != name:
            ui.abort("Name did not match. Cancelled.")

    from iblai_infra.terraform.runner import TerraformRunner

    runner = TerraformRunner(state.config)
    runner.ws = Path(state.workspace_path)
    runner.state = state
    runner.destroy()

    ui.newline()


@infra_app.command()
def status(
    name: str = typer.Argument(help="Project name"),
) -> None:
    """Show infrastructure status and workspace details for a project."""
    state = load_state(name)
    if state is None:
        ui.error(f"No infrastructure found with name: {name}")
        raise typer.Exit(1)

    status_colors = {
        "created": "#3ECF6E",
        "initialized": "#F0A830",
        "failed": "#E85454",
        "destroyed": "dim",
    }
    sc = status_colors.get(state.status, "white")

    rows = [
        ("", "[bold]General[/bold]"),
        ("Name", state.name),
        ("Provider", state.provider.upper()),
        ("Status", f"[{sc}]{state.status.upper()}[/{sc}]"),
        ("Environment", state.config.environment.value.capitalize()),
        ("Region", state.config.credentials.region),
        ("Domain", state.config.dns.base_domain),
        ("Created", state.created_at.strftime("%Y-%m-%d %H:%M UTC")),
        ("Updated", state.updated_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]

    # Workspace info
    ws = Path(state.workspace_path)
    rows.append(("", ""))
    rows.append(("", "[bold]Workspace[/bold]"))
    rows.append(("Directory", str(ws)))

    if ws.exists():
        files = sorted(f for f in ws.iterdir() if f.is_file())
        file_names = ", ".join(f.name for f in files[:8])
        if len(files) > 8:
            file_names += f" (+{len(files) - 8} more)"
        rows.append(("Files", file_names))
    else:
        rows.append(("Files", "[dim]Directory not found[/dim]"))

    # SSH key
    if state.config.ssh.private_key_path:
        rows.append(("SSH key", str(state.config.ssh.private_key_path)))

    # Setup status
    if state.setup_status:
        setup_colors = {
            "completed": "#3ECF6E",
            "running": "#F0A830",
            "failed": "#E85454",
            "pending": "dim",
        }
        ssc = setup_colors.get(state.setup_status, "white")
        rows.append(("", ""))
        rows.append(("", "[bold]Platform Setup[/bold]"))
        rows.append(("Setup status", f"[{ssc}]{state.setup_status.upper()}[/{ssc}]"))
        if state.setup_completed_at:
            rows.append(("Completed", state.setup_completed_at.strftime("%Y-%m-%d %H:%M UTC")))

    # Outputs
    if state.outputs:
        rows.append(("", ""))
        rows.append(("", "[bold]Outputs[/bold]"))
        for k, v in state.outputs.items():
            if isinstance(v, str) and v:
                label = k.replace("_", " ").capitalize()
                rows.append((label, v))

    ui.summary_panel(f"Infrastructure: {state.name}", rows)


# ---------------------------------------------------------------------------
# Shared credential resolution
# ---------------------------------------------------------------------------


def _resolve_credentials(
    profile: str | None = None,
    region: str = "us-east-1",
) -> tuple:
    """Try to authenticate with AWS. Falls back to interactive prompts if needed.

    Returns (AWSCredentials, CallerIdentity).
    """
    from rich.status import Status

    from iblai_infra.models import AWSCredentials, AuthMethod
    from iblai_infra.providers.aws import validate_credentials

    # 1. If --profile was explicitly passed, try it directly
    if profile:
        creds = AWSCredentials(method=AuthMethod.PROFILE, profile=profile, region=region)
        with Status("[info]Authenticating...[/info]", console=ui.console):
            try:
                identity = validate_credentials(creds)
                creds.account_id = identity.account_id
                creds.arn = identity.arn
                save_session(creds)
                return creds, identity
            except ValueError:
                pass
        ui.warning(f"Profile [highlight]{profile}[/highlight] failed to authenticate.")
        ui.newline()

    # 2. Try saved session
    saved = load_session()
    if saved:
        creds, identity = saved
        user = identity.arn.split("/")[-1] if identity.arn else "unknown"
        ui.success(f"Authenticated — [highlight]{user}[/highlight] ({creds.account_id})")
        return creds, identity

    # 3. Interactive credentials wizard
    from iblai_infra.prompts.credentials import prompt_credentials

    creds = prompt_credentials(show_step=False)

    identity_obj = type("Id", (), {"account_id": creds.account_id, "arn": creds.arn})()
    return creds, identity_obj


@infra_app.command()
def permissions(
    check: bool = typer.Option(
        False,
        "--check",
        help="Dry-run against active AWS credentials to verify permissions.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="AWS profile to check against (default: auto-detect).",
    ),
    region: str = typer.Option(
        "us-east-1",
        "--region",
        "-r",
        help="AWS region for the check.",
    ),
) -> None:
    """Show required IAM permissions. Use --check to verify against your credentials."""
    import json

    from iblai_infra.providers.aws import REQUIRED_IAM_POLICY

    ui.newline()
    ui.info("Minimum IAM permissions required for [highlight]iblai infra provision[/highlight]")

    policy_json = json.dumps(REQUIRED_IAM_POLICY, indent=2)
    from rich.syntax import Syntax

    ui.newline()
    ui.console.print(
        Syntax(policy_json, "json", theme="monokai", padding=1),
    )
    ui.newline()
    ui.muted("Attach this policy to your IAM user or role before provisioning.")

    if not check:
        ui.newline()
        ui.muted(
            "Run [brand]iblai infra permissions --check[/brand] to verify"
            " your credentials have these permissions."
        )
        ui.newline()
        return

    # ----- Dry-run permission check -----
    from rich.status import Status

    from iblai_infra.providers.aws import (
        check_permissions,
        get_session,
    )

    creds, identity = _resolve_credentials(profile=profile, region=region)
    ui.newline()

    # Run checks
    session = get_session(creds)
    with Status("[info]Checking permissions...[/info]", console=ui.console):
        results = check_permissions(session)

    # Display results
    table = Table(
        title=f"[bold {ui.IBL_BLUE}]Permission Check Results[/]",
        border_style=ui.IBL_NAVY,
        header_style=f"bold {ui.IBL_BLUE_LIGHT}",
        padding=(0, 1),
    )
    table.add_column("Service", style="bold white", min_width=24)
    table.add_column("Used For", style="dim", min_width=30)
    table.add_column("Status", min_width=10, justify="center")

    passed = 0
    failed = 0
    for r in results:
        if r.passed:
            status_display = "[bold #3ECF6E]\u2713 OK[/]"
            passed += 1
        else:
            status_display = "[bold #E85454]\u2717 DENIED[/]"
            failed += 1
        table.add_row(r.service, r.description, status_display)

    ui.console.print(table)
    ui.newline()

    if failed == 0:
        ui.success(f"All {passed} permission checks passed. You're ready to provision.")
    else:
        ui.error(f"{failed} of {passed + failed} checks failed.")
        ui.newline()
        for r in results:
            if not r.passed:
                ui.muted(f"  {r.service}: {r.error}")
        ui.newline()
        ui.info(
            "Attach the IAM policy above to your user/role and retry with"
            " [highlight]iblai infra permissions --check[/highlight]"
        )

    ui.newline()


@infra_app.command(name="list")
def list_cmd() -> None:
    """List all provisioned environments."""
    states = [s for s in list_all_states() if s.status != "destroyed"]

    if not states:
        ui.newline()
        ui.info("No managed infrastructure found.")
        ui.muted("Run [brand]iblai infra provision[/brand] to create your first environment.")
        ui.newline()
        return

    ui.newline()

    table = Table(
        title=f"[bold {ui.IBL_BLUE}]Managed Environments[/]",
        border_style=ui.IBL_NAVY,
        header_style=f"bold {ui.IBL_BLUE_LIGHT}",
        padding=(0, 1),
    )
    table.add_column("Name", style="bold white", min_width=16)
    table.add_column("Environment", min_width=12)
    table.add_column("Region", min_width=14)
    table.add_column("Domain", min_width=16)
    table.add_column("Infra", min_width=12, justify="center")
    table.add_column("Setup", min_width=12, justify="center")
    table.add_column("Created", min_width=10)

    status_colors = {
        "created": "#3ECF6E",
        "initialized": "#F0A830",
        "failed": "#E85454",
        "destroyed": "dim",
    }

    setup_colors = {
        "completed": "#3ECF6E",
        "running": "#F0A830",
        "failed": "#E85454",
        "pending": "dim",
    }

    for s in states:
        sc = status_colors.get(s.status, "white")
        setup_status = s.setup_status or "—"
        if s.setup_status:
            ssc = setup_colors.get(s.setup_status, "white")
            setup_display = f"[{ssc}]{s.setup_status}[/{ssc}]"
        else:
            setup_display = "[dim]\u2014[/dim]"

        table.add_row(
            s.name,
            s.config.environment.value.capitalize(),
            s.config.credentials.region,
            s.config.dns.base_domain,
            f"[{sc}]{s.status}[/{sc}]",
            setup_display,
            s.created_at.strftime("%Y-%m-%d"),
        )

    ui.console.print(table)
    ui.newline()
    ui.muted(
        f"  {len(states)} environment(s) found."
        " Use [brand]iblai infra status <name>[/brand] for details."
    )
    ui.newline()
