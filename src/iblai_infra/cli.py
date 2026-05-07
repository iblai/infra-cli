"""CLI entry point — `iblai infra <command>` structure.

Root:  iblai --version | --help
Group: iblai infra provision | retry | setup | destroy | status | list
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from iblai_infra import __version__, ui
from iblai_infra.env_utils import load_env_file, mask
from iblai_infra.terraform.state import (
    add_ingress,
    claim_ingress,
    configure_ingress_lock,
    get_ingress_status,
    list_all_states,
    load_ingress,
    load_session,
    load_state,
    release_ingress_lock,
    remove_ingress,
    save_session,
    save_state,
)

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

# ---------------------------------------------------------------------------
# Subcommand group: `iblai infra ingress`
# ---------------------------------------------------------------------------

ingress_app = typer.Typer(
    name="ingress",
    help="Manage pre-provisioned ingress endpoints (domains, certs, DNS).",
    no_args_is_help=True,
)
infra_app.add_typer(ingress_app, name="ingress")


@ingress_app.command("list")
def ingress_list() -> None:
    """List registered ingress endpoints."""
    entries = load_ingress()
    if not entries:
        ui.info("No ingress endpoints registered.")
        ui.muted("Add one with: [brand]iblai infra ingress add <name> <domain>[/brand]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Domain")
    table.add_column("Added")

    for e in entries:
        table.add_row(e.name, e.domain, e.created_at.strftime("%Y-%m-%d"))

    ui.console.print(table)


@ingress_app.command("add")
def ingress_add(
    name: str = typer.Argument(help="Short name for the ingress (e.g. stg1)"),
    domain: str = typer.Argument(help="Base domain (e.g. stg1.iblai.org)"),
) -> None:
    """Register a pre-provisioned ingress endpoint."""
    try:
        entry = add_ingress(name, domain)
        ui.success(f"Added ingress [highlight]{entry.name}[/highlight] ({entry.domain})")
    except ValueError as e:
        ui.error(str(e))
        raise typer.Exit(1)


@ingress_app.command("remove")
def ingress_remove(
    name: str = typer.Argument(help="Name of the ingress to remove"),
) -> None:
    """Remove an ingress endpoint from the registry."""
    if remove_ingress(name):
        ui.success(f"Removed ingress [highlight]{name}[/highlight]")
    else:
        ui.error(f"No ingress found with name: {name}")
        raise typer.Exit(1)


@ingress_app.command("configure")
def ingress_configure(
    bucket: str = typer.Option(..., "--bucket", help="S3 bucket for lock storage"),
    prefix: str = typer.Option("ingress-locks", "--prefix", help="S3 key prefix"),
) -> None:
    """Configure S3 as the lock backend for ingress slot management."""
    configure_ingress_lock(bucket=bucket, prefix=prefix)
    ui.success(f"Lock backend: [highlight]s3://{bucket}/{prefix}/[/highlight]")


@ingress_app.command("status")
def ingress_status() -> None:
    """Show ingress endpoints with their claim status."""
    entries = load_ingress()
    if not entries:
        ui.info("No ingress endpoints registered.")
        return

    statuses = get_ingress_status()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Domain")
    table.add_column("Status")
    table.add_column("Claimed By")
    table.add_column("Claimed At")

    for entry, lock in statuses:
        if lock:
            table.add_row(
                entry.name,
                entry.domain,
                "[red]claimed[/red]",
                lock.get("claimed_by", ""),
                lock.get("claimed_at", "")[:19],
            )
        else:
            table.add_row(entry.name, entry.domain, "[green]free[/green]", "", "")

    ui.console.print(table)


@ingress_app.command("claim")
def ingress_claim(
    name: str | None = typer.Argument(None, help="Specific slot to claim (picks first free if omitted)"),
    by: str = typer.Option("", "--by", help="Identifier for who is claiming (e.g. run ID)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the domain (for CI piping)"),
) -> None:
    """Claim a free ingress slot. Prints the domain on success."""
    result = claim_ingress(name=name, claimed_by=by)
    if result is None:
        if name:
            ui.error(f"Slot '{name}' is not available (already claimed or not registered).")
        else:
            ui.error("No free ingress slots available.")
        raise typer.Exit(1)

    slot_name, domain = result
    if quiet:
        ui.console.print(domain, highlight=False)
    else:
        ui.success(f"Claimed [highlight]{slot_name}[/highlight] ({domain})")


@ingress_app.command("release")
def ingress_release(
    name: str = typer.Argument(help="Slot name to release"),
) -> None:
    """Release a claimed ingress slot."""
    if release_ingress_lock(name):
        ui.success(f"Released [highlight]{name}[/highlight]")
    else:
        ui.error(f"Slot '{name}' is not currently claimed.")
        raise typer.Exit(1)


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
        ("iblai infra provision-env", "Provision a fresh single-server from .env (no AMI)"),
        ("iblai infra launch-env", "Launch from .env file (interactive confirm)"),
        ("iblai infra launch --ami-id ...", "Launch from AMI (non-interactive, CI/CD)"),
        ("iblai infra service-update", "Update images and restart services"),
        ("iblai infra destroy <name>", "Destroy existing infrastructure"),
        ("iblai infra status <name>", "Show infrastructure details and outputs"),
        ("iblai infra list", "List all managed environments"),
        ("iblai infra ingress list|add|remove", "Manage pre-provisioned ingress endpoints"),
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
            questionary.Choice("Manage ingress endpoints", value="ingress"),
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
    elif action == "ingress":
        ctx.invoke(ingress_list)
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

    from iblai_infra.app import show_results, _offer_setup
    show_results(state.config, outputs, ws)
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
    domain: str | None = typer.Option(None, "--domain", help="Base domain (e.g. ami.iblai.org)"),
    ingress_name: str | None = typer.Option(None, "--ingress", help="Ingress endpoint name (resolves to domain)"),
    hosted_zone_id: str = typer.Option(..., "--hosted-zone-id", help="Route53 hosted zone ID"),
    aws_key_id: str = typer.Option(..., "--aws-key-id", help="AWS access key ID"),
    aws_secret_key: str = typer.Option(..., "--aws-secret-key", help="AWS secret access key"),
    ssh_public_key: str = typer.Option(..., "--ssh-public-key", help="SSH public key material"),
    ssh_key: Path = typer.Option(..., "--ssh-key", help="Path to SSH private key"),
    git_token: str = typer.Option(..., "--git-token", help="GitHub Personal Access Token"),
    admin_email: str = typer.Option("", "--admin-email", help="Admin email address (required for single/multi-server, ignored for call-server)"),
    admin_password: str = typer.Option("", "--admin-password", help="Admin password (required for single/multi-server, ignored for call-server)"),
    vpn_ip: str = typer.Option(..., "--vpn-ip", help="IP address allowed SSH access"),
    name: str | None = typer.Option(None, "--name", help="Project name (auto-generated from domain if omitted)"),
    ssh_user: str = typer.Option("ubuntu", "--ssh-user", help="SSH user"),
    aws_region: str = typer.Option("us-east-1", "--aws-region", help="AWS region"),
    instance_type: str = typer.Option("t3.2xlarge", "--instance-type", help="EC2 instance type"),
    volume_size: int = typer.Option(200, "--volume-size", help="Root volume size in GB"),
    environment: str = typer.Option("staging", "--environment", help="Environment (dev, staging, prod)"),
    cli_tag: str = typer.Option("3.19.0", "--cli-tag", help="iblai-cli-ops release tag"),
    github_org: str = typer.Option("iblai", "--github-org", help="GitHub org owning the private CLI ops + prod images repos"),
    cli_ops_repo: str = typer.Option("iblai-cli-ops", "--cli-ops-repo", help="CLI ops repo, or 'repo/subdir' to install from a subdirectory of a monorepo"),
    prod_images_repo: str = typer.Option("iblai-prod-images", "--prod-images-repo", help="Prod images repo, or 'repo/subdir' to install from a subdirectory of a monorepo"),
    admin_username: str = typer.Option("ibl_admin", "--admin-username", help="Admin username"),
    openai_key: str = typer.Option("", "--openai-key", help="OpenAI API key (optional)"),
    enable_ai: bool = typer.Option(True, "--enable-ai/--no-ai", help="Enable AI features"),
    create_playwright_platforms: bool = typer.Option(
        False,
        "--create-playwright-platforms/--no-create-playwright-platforms",
        help="Create 8 spa-tests-* platforms + 4 browser superusers + 1 student user for Playwright tests",
    ),
    # SMTP — `--smtp-host` is the trigger; if empty, the role no-ops
    smtp_host: str = typer.Option("", "--smtp-host", help="SMTP host (e.g. email-smtp.us-east-1.amazonaws.com). Setting this enables SMTP."),
    smtp_port: int = typer.Option(587, "--smtp-port", help="SMTP port (default 587 for STARTTLS)"),
    smtp_username: str = typer.Option("", "--smtp-username", help="SMTP username (e.g. SES IAM access key id)"),
    smtp_password: str = typer.Option("", "--smtp-password", help="SMTP password"),
    smtp_sender_email: str = typer.Option("", "--smtp-sender-email", help="SMTP sender email (From: address)"),
    smtp_use_tls: bool = typer.Option(True, "--smtp-use-tls/--no-smtp-use-tls", help="Use STARTTLS (default true)"),
    smtp_use_ssl: bool = typer.Option(False, "--smtp-use-ssl/--no-smtp-use-ssl", help="Use implicit SSL/SMTPS (default false)"),
    # Stripe — `--stripe-secret-key` is the trigger; if empty, the role no-ops
    stripe_secret_key: str = typer.Option("", "--stripe-secret-key", help="Stripe secret key (sk_test_/sk_live_). Setting this enables Stripe."),
    stripe_pub_key: str = typer.Option("", "--stripe-pub-key", help="Stripe publishable key (pk_test_/pk_live_)"),
    stripe_mode: str = typer.Option("test", "--stripe-mode", help="Stripe mode: test or live (default test)"),
    stripe_pricing_table_id: str = typer.Option("", "--stripe-pricing-table-id", help="Stripe pricing table id (prctbl_...)"),
    stripe_pricing_table_id_returning: str = typer.Option("", "--stripe-pricing-table-id-returning", help="Stripe pricing table id for returning users"),
    stripe_webhook_secret: str = typer.Option("", "--stripe-webhook-secret", help="Stripe webhook signing secret (whsec_...)"),
    stripe_connect_webhook_secret: str = typer.Option("", "--stripe-connect-webhook-secret", help="Stripe Connect webhook signing secret (whsec_...)"),
    # Google SSO — `--google-sso-client-id` is the trigger; if empty, the role no-ops
    google_sso_client_id: str = typer.Option("", "--google-sso-client-id", help="Google OAuth Client ID. Setting this enables the Google SSO ansible role."),
    google_sso_client_secret: str = typer.Option("", "--google-sso-client-secret", help="Google OAuth Client Secret"),
    google_sso_organization: str = typer.Option("", "--google-sso-organization", help="Organization short name to attach to the OAuth2ProviderConfig (optional)"),
    # Platform name — drives SSO backend_name + platform_key. Always populated; defaults to "main"
    platform_name: str = typer.Option("main", "--platform-name", help="Platform identifier (lowercase). Used to derive SSO backend_name (<platform>-oauth2) and other_settings.platform_key. Default 'main'."),
    # Microsoft SSO — `--microsoft-sso-client-id` is the trigger; if empty, the role no-ops
    microsoft_sso_client_id: str = typer.Option("", "--microsoft-sso-client-id", help="Microsoft Azure AD Application (Client) ID. Setting this enables the Microsoft SSO ansible role."),
    microsoft_sso_client_secret: str = typer.Option("", "--microsoft-sso-client-secret", help="Microsoft Azure AD Client Secret value"),
    microsoft_sso_tenant_id: str = typer.Option("", "--microsoft-sso-tenant-id", help="Microsoft Azure AD Tenant ID (used to construct OIDC endpoint + logout URL)"),
    microsoft_sso_organization: str = typer.Option("", "--microsoft-sso-organization", help="Organization short name to attach to the OAuth2ProviderConfig (optional)"),
    deployment_type: str = typer.Option("single-server", "--deployment-type", help="single-server, multi-server, or call-server"),
    app_server_count: int = typer.Option(2, "--app-server-count", help="Number of app servers (multi-server only)"),
    services_instance_type: str = typer.Option("t3.2xlarge", "--services-instance-type", help="Services server instance type (multi-server only)"),
    services_volume_size: int = typer.Option(500, "--services-volume-size", help="Services server volume in GB (multi-server only)"),
    enable_mysql: bool = typer.Option(False, "--enable-mysql/--no-mysql", help="Enable managed MySQL RDS (multi-server only)"),
    enable_postgres: bool = typer.Option(False, "--enable-postgres/--no-postgres", help="Enable managed PostgreSQL RDS (multi-server only)"),
    enable_redis: bool = typer.Option(False, "--enable-redis/--no-redis", help="Enable managed Redis ElastiCache (multi-server only)"),
    enable_sip: bool = typer.Option(False, "--enable-sip/--no-sip", help="Open LiveKit SIP ports (call-server only)"),
) -> None:
    """Launch IBL platform from a pre-built AMI. Non-interactive, CI/CD-friendly.

    Provisions AWS infrastructure (VPC, ALB, ACM certs, Route53, EC2) via Terraform,
    then configures the platform (domain, secrets, service restarts) via Ansible.

    Provide either --domain or --ingress (resolved from registered endpoints).

    For a standalone LiveKit call server, pass --deployment-type call-server.
    This provisions an isolated 10.1.0.0/16 VPC with the full LiveKit port set
    and runs only docker/awscli/python/ibl_cli_ops/ibl_call ansible roles.
    """
    if ingress_name and not domain:
        entries = load_ingress()
        match = next((e for e in entries if e.name == ingress_name), None)
        if not match:
            ui.error(f"No ingress endpoint found: {ingress_name}")
            ui.muted("Run [brand]iblai infra ingress list[/brand] to see available endpoints.")
            raise typer.Exit(1)
        domain = match.domain
    if not domain:
        ui.error("Either --domain or --ingress is required.")
        raise typer.Exit(1)

    # call-server skips admin creation entirely; single/multi still need it
    if deployment_type != "call-server":
        if not admin_email or not admin_password:
            ui.error("--admin-email and --admin-password are required for single/multi-server deployments.")
            raise typer.Exit(1)

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
        github_org=github_org,
        cli_ops_repo=cli_ops_repo,
        prod_images_repo=prod_images_repo,
        create_playwright_platforms=create_playwright_platforms,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_sender_email=smtp_sender_email,
        smtp_use_tls=smtp_use_tls,
        smtp_use_ssl=smtp_use_ssl,
        stripe_secret_key=stripe_secret_key,
        stripe_pub_key=stripe_pub_key,
        stripe_mode=stripe_mode,
        stripe_pricing_table_id=stripe_pricing_table_id,
        stripe_pricing_table_id_returning=stripe_pricing_table_id_returning,
        stripe_webhook_secret=stripe_webhook_secret,
        stripe_connect_webhook_secret=stripe_connect_webhook_secret,
        google_sso_client_id=google_sso_client_id,
        google_sso_client_secret=google_sso_client_secret,
        google_sso_organization=google_sso_organization,
        platform_name=platform_name,
        microsoft_sso_client_id=microsoft_sso_client_id,
        microsoft_sso_client_secret=microsoft_sso_client_secret,
        microsoft_sso_tenant_id=microsoft_sso_tenant_id,
        microsoft_sso_organization=microsoft_sso_organization,
        deployment_type=deployment_type,
        app_server_count=app_server_count,
        services_instance_type=services_instance_type,
        services_volume_size=services_volume_size,
        enable_mysql=enable_mysql,
        enable_postgres=enable_postgres,
        enable_redis=enable_redis,
        enable_sip=enable_sip,
    )


@infra_app.command(name="launch-env")
def launch_env(
    env_file: Path = typer.Option(
        ".env", "--env-file", "-f",
        help="Path to .env file (default: .env in current directory)",
    ),
) -> None:
    """Launch from a .env file. Copy .env.example to .env, fill in values, then run this."""
    import questionary

    if not env_file.exists():
        ui.error(f"No .env file found at: {env_file}")
        ui.newline()
        ui.info("To get started:")
        ui.muted("  1. Copy [brand].env.example[/brand] to [brand].env[/brand]")
        ui.muted("  2. Fill in your values")
        ui.muted("  3. Run [brand]iblai infra launch-env[/brand]")
        ui.newline()
        raise typer.Exit(1)

    env = load_env_file(env_file)

    # Required variables
    required = {
        "AMI_ID": "Custom AMI ID",
        "DOMAIN": "Base domain",
        "HOSTED_ZONE_ID": "Route53 hosted zone ID",
        "AWS_ACCESS_KEY_ID": "AWS access key ID",
        "AWS_SECRET_ACCESS_KEY": "AWS secret access key",
        "SSH_PUBLIC_KEY": "SSH public key",
        "SSH_KEY_PATH": "Path to SSH private key",
        "GIT_TOKEN": "GitHub Personal Access Token",
        "ADMIN_EMAIL": "Admin email",
        "ADMIN_PASSWORD": "Admin password",
        "VPN_IP": "VPN IP for SSH access",
    }

    missing = [f"{desc} ({key})" for key, desc in required.items() if not env.get(key)]
    if missing:
        ui.error("Missing required variables in .env:")
        for m in missing:
            ui.muted(f"  - {m}")
        ui.newline()
        raise typer.Exit(1)

    # Map env vars to launch params
    ami_id = env["AMI_ID"]
    domain = env["DOMAIN"]
    hosted_zone_id = env["HOSTED_ZONE_ID"]
    aws_key_id = env["AWS_ACCESS_KEY_ID"]
    aws_secret_key = env["AWS_SECRET_ACCESS_KEY"]
    ssh_public_key = env["SSH_PUBLIC_KEY"]
    ssh_key = Path(env["SSH_KEY_PATH"]).expanduser()
    git_token = env["GIT_TOKEN"]
    admin_email = env["ADMIN_EMAIL"]
    admin_password = env["ADMIN_PASSWORD"]
    vpn_ip = env["VPN_IP"]

    # Optional with defaults
    name = env.get("NAME") or None
    ssh_user = env.get("SSH_USER", "ubuntu")
    aws_region = env.get("AWS_DEFAULT_REGION", "us-east-1")
    instance_type = env.get("INSTANCE_TYPE", "t3.2xlarge")
    volume_size = int(env.get("VOLUME_SIZE", "200"))
    environment = env.get("ENVIRONMENT", "staging")
    cli_tag = env.get("CLI_TAG", "3.19.0")
    admin_username = env.get("ADMIN_USERNAME", "ibl_admin")
    openai_key = env.get("OPENAI_API_KEY", "")
    enable_ai = env.get("ENABLE_AI", "true").lower() in ("true", "1", "yes")
    create_playwright_platforms = env.get("CREATE_PLAYWRIGHT_PLATFORMS", "false").lower() in ("true", "1", "yes")
    github_org = env.get("GITHUB_ORG", "iblai")
    cli_ops_repo = env.get("CLI_OPS_REPO", "iblai-cli-ops")
    prod_images_repo = env.get("PROD_IMAGES_REPO", "iblai-prod-images")
    smtp_host = env.get("SMTP_HOST", "")
    smtp_port = int(env.get("SMTP_PORT", "587"))
    smtp_username = env.get("SMTP_USERNAME", "")
    smtp_password = env.get("SMTP_PASSWORD", "")
    smtp_sender_email = env.get("SMTP_SENDER_EMAIL", "")
    smtp_use_tls = env.get("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
    smtp_use_ssl = env.get("SMTP_USE_SSL", "false").lower() in ("true", "1", "yes")
    stripe_secret_key = env.get("STRIPE_SECRET_KEY", "")
    stripe_pub_key = env.get("STRIPE_PUB_KEY", "")
    stripe_mode = env.get("STRIPE_MODE", "test")
    stripe_pricing_table_id = env.get("STRIPE_PRICING_TABLE_ID", "")
    stripe_pricing_table_id_returning = env.get("STRIPE_PRICING_TABLE_ID_RETURNING", "")
    stripe_webhook_secret = env.get("STRIPE_WEBHOOK_SECRET", "")
    stripe_connect_webhook_secret = env.get("STRIPE_CONNECT_WEBHOOK_SECRET", "")
    google_sso_client_id = env.get("GOOGLE_SSO_CLIENT_ID", "")
    google_sso_client_secret = env.get("GOOGLE_SSO_CLIENT_SECRET", "")
    google_sso_organization = env.get("GOOGLE_SSO_ORGANIZATION", "")
    platform_name = env.get("PLATFORM_NAME", "main")
    microsoft_sso_client_id = env.get("MICROSOFT_SSO_CLIENT_ID", "")
    microsoft_sso_client_secret = env.get("MICROSOFT_SSO_CLIENT_SECRET", "")
    microsoft_sso_tenant_id = env.get("MICROSOFT_SSO_TENANT_ID", "")
    microsoft_sso_organization = env.get("MICROSOFT_SSO_ORGANIZATION", "")

    # Show summary
    project_name = name or domain.replace(".", "-")
    if len(project_name) > 32:
        project_name = project_name[:32]

    rows = [
        ("AMI", ami_id),
        ("Domain", domain),
        ("Project", project_name),
        ("Region", aws_region),
        ("Instance", instance_type),
        ("Volume", f"{volume_size} GB"),
        ("Environment", environment),
        ("VPN IP", vpn_ip),
        ("SSH key", str(ssh_key)),
        ("AWS key", mask(aws_key_id)),
        ("Admin", f"{admin_username} ({admin_email})"),
        ("AI features", "Enabled" if enable_ai else "Disabled"),
    ]
    ui.summary_panel("Launch Configuration", rows)

    confirm = questionary.confirm(
        "Proceed with launch?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if not confirm:
        ui.abort("Cancelled.")

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
        github_org=github_org,
        cli_ops_repo=cli_ops_repo,
        prod_images_repo=prod_images_repo,
        create_playwright_platforms=create_playwright_platforms,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_sender_email=smtp_sender_email,
        smtp_use_tls=smtp_use_tls,
        smtp_use_ssl=smtp_use_ssl,
        stripe_secret_key=stripe_secret_key,
        stripe_pub_key=stripe_pub_key,
        stripe_mode=stripe_mode,
        stripe_pricing_table_id=stripe_pricing_table_id,
        stripe_pricing_table_id_returning=stripe_pricing_table_id_returning,
        stripe_webhook_secret=stripe_webhook_secret,
        stripe_connect_webhook_secret=stripe_connect_webhook_secret,
        google_sso_client_id=google_sso_client_id,
        google_sso_client_secret=google_sso_client_secret,
        google_sso_organization=google_sso_organization,
        platform_name=platform_name,
        microsoft_sso_client_id=microsoft_sso_client_id,
        microsoft_sso_client_secret=microsoft_sso_client_secret,
        microsoft_sso_tenant_id=microsoft_sso_tenant_id,
        microsoft_sso_organization=microsoft_sso_organization,
    )


@infra_app.command(name="provision-env")
def provision_env(
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-f",
        help="Path to .env file (default: .env in current directory)",
    ),
) -> None:
    """Provision a fresh single-server from a .env file (Terraform only).

    Mirrors the interactive `provision` wizard but reads every answer from
    a .env file. Single-server only — multi-server / call-server still
    require the wizard. After this completes, run
    [brand]iblai infra setup <name>[/brand] to bootstrap the VM.

    Copy .env.provision.example to .env, fill in values, then run this.
    """
    if not env_file.exists():
        ui.error(f"No .env file found at: {env_file}")
        ui.newline()
        ui.info("To get started:")
        ui.muted("  1. Copy [brand].env.provision.example[/brand] to [brand].env[/brand]")
        ui.muted("  2. Fill in your values")
        ui.muted("  3. Run [brand]iblai infra provision-env[/brand]")
        ui.newline()
        raise typer.Exit(1)

    from iblai_infra.app import show_results, show_workspace
    from iblai_infra.env_provision import build_infra_config_from_env
    from iblai_infra.terraform.runner import TerraformRunner

    env = load_env_file(env_file)
    config = build_infra_config_from_env(env)

    # Summary panel — mirrors launch-env's shape, masks the AWS key.
    rows = [
        ("Project", f"{config.project_name}-{config.environment.value}"),
        ("Region", config.credentials.region),
        ("Instance", config.compute.instance_type),
        ("Volume", f"{config.compute.volume_size} GB ({config.compute.volume_type})"),
        ("VPC CIDR", config.network.vpc_cidr),
        ("VPN IP", f"{config.network.vpn_ip}/32"),
        ("Domain", config.dns.base_domain),
        ("Cert", config.certificates.method.value),
        ("SSH", f"{config.ssh.method.value} ({config.ssh.key_name})"),
    ]
    if config.credentials.access_key_id:
        rows.append(("AWS key", mask(config.credentials.access_key_id)))
    elif config.credentials.profile:
        rows.append(("AWS profile", config.credentials.profile))
    ui.summary_panel("Provision Configuration", rows)

    ui.newline()
    ui.console.print("  [brand]Provisioning infrastructure...[/brand]")

    runner = TerraformRunner(config)
    # Mark provider so future `iblai infra list` / destroy can tell this
    # apart from interactive (`aws`) and AMI (`launch`) provisions.
    runner.state.provider = "provision-env"
    runner.setup()
    show_workspace(runner.ws)
    runner.init()

    add_count = runner.plan()
    if add_count == 0:
        ui.warning("No resources to create. Infrastructure may already exist.")
        return

    outputs = runner.apply()
    show_results(config, outputs, runner.ws)

    ui.muted(
        f"Run [brand]iblai infra setup {config.project_name}[/brand] to bootstrap the VM."
    )
    ui.newline()


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
    github_org: str = "iblai",
    cli_ops_repo: str = "iblai-cli-ops",
    prod_images_repo: str = "iblai-prod-images",
    create_playwright_platforms: bool = False,
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_username: str = "",
    smtp_password: str = "",
    smtp_sender_email: str = "",
    smtp_use_tls: bool = True,
    smtp_use_ssl: bool = False,
    stripe_secret_key: str = "",
    stripe_pub_key: str = "",
    stripe_mode: str = "test",
    stripe_pricing_table_id: str = "",
    stripe_pricing_table_id_returning: str = "",
    stripe_webhook_secret: str = "",
    stripe_connect_webhook_secret: str = "",
    google_sso_client_id: str = "",
    google_sso_client_secret: str = "",
    google_sso_organization: str = "",
    platform_name: str = "main",
    microsoft_sso_client_id: str = "",
    microsoft_sso_client_secret: str = "",
    microsoft_sso_tenant_id: str = "",
    microsoft_sso_organization: str = "",
    deployment_type: str = "single-server",
    app_server_count: int = 2,
    services_instance_type: str = "t3.2xlarge",
    services_volume_size: int = 500,
    enable_mysql: bool = False,
    enable_postgres: bool = False,
    enable_redis: bool = False,
    enable_sip: bool = False,
) -> None:
    """Provision infrastructure from AMI and configure platform. Non-interactive."""
    import os
    import shutil
    from datetime import datetime, timezone

    from iblai_infra.ansible.runner import AnsibleRunner, CALL_ROLE_LABELS, LAUNCH_ROLE_LABELS
    from iblai_infra.models import (
        AWSCredentials,
        AuthMethod,
        CallServerConfig,
        CertificateConfig,
        CertMethod,
        ComputeConfig,
        DeploymentType,
        DNSConfig,
        Environment,
        InfraConfig,
        MultiServerConfig,
        NetworkConfig,
        ProjectState,
        SetupConfig,
        SSHConfig,
        SSHKeyMethod,
        generate_password,
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

    # Map deployment type string
    deploy_type_map = {
        "multi-server": DeploymentType.MULTI,
        "call-server": DeploymentType.CALL,
    }
    deploy_type = deploy_type_map.get(deployment_type, DeploymentType.SINGLE)

    # Topology-specific optional configs
    multi_server: MultiServerConfig | None = None
    call_server: CallServerConfig | None = None

    if deploy_type == DeploymentType.MULTI:
        multi_server = MultiServerConfig(
            app_server_count=app_server_count,
            app_server_instance_type=instance_type,
            app_server_volume_size=volume_size,
            services_instance_type=services_instance_type,
            services_volume_size=services_volume_size,
            enable_mysql=enable_mysql,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
            mysql_password=generate_password() if enable_mysql else None,
            postgres_password=generate_password() if enable_postgres else None,
            redis_auth_token=generate_password(32) if enable_redis else None,
        )
    elif deploy_type == DeploymentType.CALL:
        call_server = CallServerConfig(
            instance_type=instance_type,
            volume_size=volume_size,
            vpc_cidr="10.1.0.0/16",
            enable_sip=enable_sip,
        )

    # Check prerequisites
    if shutil.which("terraform") is None:
        ui.error("terraform not found. Install from https://www.terraform.io/downloads")
        raise typer.Exit(1)
    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found. Install with: pip install ansible-core")
        raise typer.Exit(1)

    # call-server uses an isolated 10.1/16 VPC to avoid clashing with
    # single/multi-server deployments running alongside.
    vpc_cidr = "10.1.0.0/16" if deploy_type == DeploymentType.CALL else "10.0.0.0/16"

    # Build InfraConfig
    infra_config = InfraConfig(
        project_name=project_name,
        environment=env,
        deployment_type=deploy_type,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY,
            access_key_id=aws_key_id,
            secret_access_key=aws_secret_key,
            region=aws_region,
        ),
        network=NetworkConfig(vpc_cidr=vpc_cidr, vpn_ip=vpn_ip),
        compute=ComputeConfig(
            instance_type=instance_type,
            volume_size=volume_size,
            ami_id=ami_id,
        ),
        multi_server=multi_server,
        call_server=call_server,
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

    # Same private-resource notice the interactive setup flow prints +
    # confirms — but this is `iblai infra launch` / `launch-env`, so we
    # surface the requirement as a heads-up without prompting. The
    # operator is committed to phase 2 by the time they see this; if
    # they realize they don't have access, ctrl-C + `iblai infra
    # destroy` is the recovery.
    ui.private_access_notice()
    ui.newline()

    setup_config = SetupConfig(
        ssh_private_key_path=ssh_key,
        ssh_user=ssh_user,
        target_host=instance_ip,
        base_domain=domain,
        env_config=("call-only" if deploy_type == DeploymentType.CALL else "single-server"),
        cli_ops_release_tag=cli_tag,
        enable_ai=enable_ai,
        create_playwright_platforms=create_playwright_platforms,
        smtp_enabled=bool(smtp_host),
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_sender_email=smtp_sender_email,
        smtp_use_tls=smtp_use_tls,
        smtp_use_ssl=smtp_use_ssl,
        stripe_enabled=bool(stripe_secret_key),
        stripe_mode=stripe_mode,
        stripe_secret_key=stripe_secret_key,
        stripe_pub_key=stripe_pub_key,
        stripe_pricing_table_id=stripe_pricing_table_id,
        stripe_pricing_table_id_returning=stripe_pricing_table_id_returning,
        stripe_webhook_secret=stripe_webhook_secret,
        stripe_connect_webhook_secret=stripe_connect_webhook_secret,
        google_sso_enabled=bool(google_sso_client_id),
        google_sso_client_id=google_sso_client_id,
        google_sso_client_secret=google_sso_client_secret,
        google_sso_organization=google_sso_organization,
        platform_name=(platform_name or "main").strip().lower(),
        microsoft_sso_enabled=bool(microsoft_sso_client_id),
        microsoft_sso_client_id=microsoft_sso_client_id,
        microsoft_sso_client_secret=microsoft_sso_client_secret,
        microsoft_sso_tenant_id=microsoft_sso_tenant_id,
        microsoft_sso_organization=microsoft_sso_organization,
        aws_access_key_id=aws_key_id,
        aws_secret_access_key=aws_secret_key,
        aws_default_region=aws_region,
        git_access_token=git_token,
        github_org=github_org,
        cli_ops_repo=cli_ops_repo,
        prod_images_repo=prod_images_repo,
        openai_api_key=openai_key,
        admin_username=admin_username,
        admin_email=admin_email,
        admin_password=admin_password,
    )

    if deploy_type == DeploymentType.CALL:
        ansible_runner = AnsibleRunner(
            state, setup_config,
            playbook="call_playbook.yml",
            role_labels=CALL_ROLE_LABELS,
        )
    else:
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


@infra_app.command(name="service-update")
def service_update(
    host: str | None = typer.Option(None, "--host", help="Target server IP (for existing servers)"),
    ssh_key: Path = typer.Option(..., "--ssh-key", help="Path to SSH private key"),
    git_token: str = typer.Option(..., "--git-token", help="GitHub Personal Access Token"),
    ssh_user: str = typer.Option("ubuntu", "--ssh-user", help="SSH user"),
    name: str | None = typer.Option(None, "--name", help="Project name (auto-generated if omitted)"),
    ami_id: str | None = typer.Option(None, "--ami-id", help="Launch EC2 from this AMI before updating"),
    subnet_id: str | None = typer.Option(None, "--subnet-id", help="Subnet to launch into (with --ami-id)"),
    security_group_id: str | None = typer.Option(None, "--security-group-id", help="Security group for EC2 (with --ami-id)"),
    target_group_arn: str | None = typer.Option(None, "--target-group-arn", help="ALB target group to register instance (with --ami-id)"),
    key_pair_name: str | None = typer.Option(None, "--key-pair-name", help="AWS key pair name for EC2 (with --ami-id)"),
    instance_type: str = typer.Option("t3.2xlarge", "--instance-type", help="EC2 instance type (with --ami-id)"),
    volume_size: int = typer.Option(200, "--volume-size", help="Root volume size in GB (with --ami-id)"),
    aws_key_id: str | None = typer.Option(None, "--aws-key-id", help="AWS access key ID (with --ami-id)"),
    aws_secret_key: str | None = typer.Option(None, "--aws-secret-key", help="AWS secret access key (with --ami-id)"),
    aws_region: str = typer.Option("us-east-1", "--aws-region", help="AWS region (with --ami-id)"),
    prod_images_tag: str = typer.Option("main", "--prod-images-tag", help="iblai-prod-images git tag or branch"),
) -> None:
    """Update container images and restart services.

    Two modes:
      --host: update an existing server directly
      --ami-id: launch EC2 from AMI, update services, register in target group
    """
    if ami_id:
        missing = []
        if not subnet_id:
            missing.append("--subnet-id")
        if not security_group_id:
            missing.append("--security-group-id")
        if not target_group_arn:
            missing.append("--target-group-arn")
        if not key_pair_name:
            missing.append("--key-pair-name")
        if not aws_key_id:
            missing.append("--aws-key-id")
        if not aws_secret_key:
            missing.append("--aws-secret-key")
        if missing:
            ui.error(f"Missing required flags for AMI launch: {', '.join(missing)}")
            raise typer.Exit(1)

        _run_service_update_from_ami(
            ami_id=ami_id, subnet_id=subnet_id, security_group_id=security_group_id,
            target_group_arn=target_group_arn, key_pair_name=key_pair_name,
            instance_type=instance_type, volume_size=volume_size,
            aws_key_id=aws_key_id, aws_secret_key=aws_secret_key,
            aws_region=aws_region, ssh_key=ssh_key, git_token=git_token,
            ssh_user=ssh_user, name=name, prod_images_tag=prod_images_tag,
        )
    elif host:
        _run_service_update(
            host=host, ssh_key=ssh_key, git_token=git_token,
            ssh_user=ssh_user, name=name, prod_images_tag=prod_images_tag,
        )
    else:
        ui.error("Either --host or --ami-id is required.")
        raise typer.Exit(1)


def _run_service_update(
    *,
    host: str,
    ssh_key: Path,
    git_token: str,
    ssh_user: str,
    name: str | None,
    prod_images_tag: str = "main",
) -> None:
    """Install latest images and restart all services."""
    import os
    import shutil
    from datetime import datetime, timezone

    from iblai_infra.ansible.runner import AnsibleRunner, SERVICE_UPDATE_ROLE_LABELS
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
    from iblai_infra.terraform.state import WORKSPACE_ROOT

    # Derive project name
    project_name = name or host.replace(".", "-")
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

    # Check ansible
    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found. Install with: pip install ansible-core")
        raise typer.Exit(1)

    # Build SetupConfig with minimal values (only SSH + git needed)
    setup_config = SetupConfig(
        ssh_private_key_path=ssh_key,
        ssh_user=ssh_user,
        target_host=host,
        base_domain="service-update",
        prod_images_tag=prod_images_tag,
        aws_access_key_id="",
        aws_secret_access_key="",
        aws_default_region="us-east-1",
        git_access_token=git_token,
    )

    # Create or update state
    workspace_path = str(WORKSPACE_ROOT / f"{project_name}-service-update")
    existing = load_state(project_name)
    if existing is not None:
        state = existing
        state.outputs = {"instance_public_ip": host}
    else:
        state = ProjectState(
            name=project_name,
            provider="service-update",
            status="created",
            config=InfraConfig(
                project_name=project_name,
                environment=Environment.DEV,
                credentials=AWSCredentials(
                    method=AuthMethod.ACCESS_KEY,
                    access_key_id="",
                    secret_access_key="",
                    region="us-east-1",
                ),
                network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="0.0.0.0"),
                compute=ComputeConfig(),
                ssh=SSHConfig(
                    method=SSHKeyMethod.EXISTING_FILE,
                    key_name="service-update",
                    private_key_path=ssh_key,
                ),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="service-update"),
            ),
            outputs={"instance_public_ip": host},
            workspace_path=workspace_path,
        )
    save_state(state)

    ui.info(f"Updating services on [highlight]{host}[/highlight]")
    ui.info(f"Project: [highlight]{project_name}[/highlight]")
    ui.newline()

    runner = AnsibleRunner(
        state, setup_config,
        playbook="service_update_playbook.yml",
        role_labels=SERVICE_UPDATE_ROLE_LABELS,
    )

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
        ui.abort("Interrupted.")

    if success:
        ui.newline()
        ui.success(f"Service update complete on [highlight]{host}[/highlight]")
        ui.newline()
    else:
        raise typer.Exit(1)


def _run_service_update_from_ami(
    *,
    ami_id: str,
    subnet_id: str,
    security_group_id: str,
    target_group_arn: str,
    key_pair_name: str,
    instance_type: str,
    volume_size: int,
    aws_key_id: str,
    aws_secret_key: str,
    aws_region: str,
    ssh_key: Path,
    git_token: str,
    ssh_user: str,
    name: str | None,
    prod_images_tag: str = "main",
) -> None:
    """Launch EC2 from AMI, run service update, register in target group."""
    import os
    import shutil
    from datetime import datetime, timezone

    from iblai_infra.ansible.runner import AnsibleRunner, SERVICE_UPDATE_ROLE_LABELS
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
    from iblai_infra.providers.aws import (
        launch_instance,
        register_target,
        terminate_instance,
        wait_for_instance_running,
    )
    from iblai_infra.terraform.state import WORKSPACE_ROOT

    # Derive project name
    project_name = name or f"su-{ami_id[-8:]}"
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

    # Check ansible
    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found. Install with: pip install ansible-core")
        raise typer.Exit(1)

    # Create boto3 session
    import boto3
    session = boto3.Session(
        aws_access_key_id=aws_key_id,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )

    # ---- Phase 1: Launch EC2 ----
    ui.info(f"Launching EC2 from AMI [highlight]{ami_id}[/highlight]")

    instance_id = launch_instance(
        session=session,
        ami_id=ami_id,
        instance_type=instance_type,
        key_pair_name=key_pair_name,
        subnet_id=subnet_id,
        security_group_id=security_group_id,
        volume_size=volume_size,
        name_tag=f"{project_name}-server",
    )
    ui.info(f"Instance launched: [highlight]{instance_id}[/highlight]")
    ui.info("Waiting for instance to be running...")

    host = wait_for_instance_running(session, instance_id)
    if not host:
        ui.error("Instance has no public IP. Check subnet settings (map_public_ip_on_launch).")
        terminate_instance(session, instance_id)
        raise typer.Exit(1)

    ui.success(f"Instance running: [highlight]{host}[/highlight]")
    ui.newline()

    # ---- Phase 2: Service Update (Ansible) ----
    ui.info("Running service update...")
    ui.newline()

    setup_config = SetupConfig(
        ssh_private_key_path=ssh_key,
        ssh_user=ssh_user,
        target_host=host,
        base_domain="service-update",
        prod_images_tag=prod_images_tag,
        aws_access_key_id="",
        aws_secret_access_key="",
        aws_default_region=aws_region,
        git_access_token=git_token,
    )

    workspace_path = str(WORKSPACE_ROOT / f"{project_name}-service-update")
    state = ProjectState(
        name=project_name,
        provider="service-update",
        status="created",
        config=InfraConfig(
            project_name=project_name,
            environment=Environment.DEV,
            credentials=AWSCredentials(
                method=AuthMethod.ACCESS_KEY,
                access_key_id="",
                secret_access_key="",
                region=aws_region,
            ),
            network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="0.0.0.0"),
            compute=ComputeConfig(),
            ssh=SSHConfig(
                method=SSHKeyMethod.EXISTING_FILE,
                key_name=key_pair_name,
                private_key_path=ssh_key,
            ),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="service-update"),
        ),
        outputs={"instance_public_ip": host, "instance_id": instance_id},
        workspace_path=workspace_path,
    )
    save_state(state)

    runner = AnsibleRunner(
        state, setup_config,
        playbook="service_update_playbook.yml",
        role_labels=SERVICE_UPDATE_ROLE_LABELS,
    )

    if not runner.preflight():
        ui.error("Pre-flight failed. Terminating instance.")
        terminate_instance(session, instance_id)
        raise typer.Exit(1)

    runner.setup()

    try:
        success = runner.run()
    except KeyboardInterrupt:
        ui.newline()
        state.setup_status = "failed"
        state.updated_at = datetime.now(timezone.utc)
        save_state(state)
        ui.warning(f"Interrupted. Instance [highlight]{instance_id}[/highlight] is still running.")
        ui.muted(f"Terminate manually: aws ec2 terminate-instances --instance-ids {instance_id}")
        ui.abort("Interrupted.")

    if not success:
        ui.error(f"Service update failed. Instance [highlight]{instance_id}[/highlight] is still running.")
        ui.muted(f"Terminate manually: aws ec2 terminate-instances --instance-ids {instance_id}")
        raise typer.Exit(1)

    # ---- Phase 3: Register in target group ----
    ui.newline()
    ui.info(f"Registering instance in target group...")
    register_target(session, target_group_arn, instance_id)
    ui.success(f"Instance [highlight]{instance_id}[/highlight] registered in target group")
    ui.newline()

    ui.success(f"Service update complete!")
    ui.info(f"Instance: [highlight]{instance_id}[/highlight] ({host})")
    ui.info(f"SSH: [highlight]ssh -i {ssh_key} {ssh_user}@{host}[/highlight]")
    ui.newline()


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

    # Same upfront access gate as the other interactive setup paths —
    # bail before prompts if the operator lacks access.
    _confirm_private_access_or_abort()

    try:
        setup_config = prompt_resetup(state)
    except KeyboardInterrupt:
        ui.newline()
        ui.abort("Interrupted.")

    _confirm_and_run(state, setup_config, f"iblai infra resetup {name}")


def _confirm_private_access_or_abort() -> None:
    """Show the IBL private-resource prerequisite notice and gate the flow.

    Called at the very top of every interactive setup path — before any
    prompts collect input — so an operator who lacks access can bail
    cleanly without typing GitHub PATs, AWS keys, or repo names. The
    notice intentionally shows the canonical default repo names; if the
    operator IS proceeding (yes), they can still override the repo names
    at the credentials step a moment later.
    """
    import questionary

    ui.private_access_notice()
    have_access = questionary.confirm(
        "Do you have access to all three private resources above?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if not have_access:
        ui.abort(
            "Cancelled. Request access at https://ibl.ai/contact/ "
            "and re-run when ready."
        )


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

    # Show prerequisites + ask for access confirmation BEFORE collecting
    # any prompts. If the operator doesn't have access to the private
    # packages or ECR, they should bail here — not after pasting GitHub
    # PAT, repo names, AWS keys, etc. The notice repeats the canonical
    # default repo names; the credentials prompt collects optional
    # overrides for forks. The final summary at `_confirm_and_run` is
    # the last gate before ansible actually runs.
    _confirm_private_access_or_abort()

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

    # Same upfront access gate as `_run_setup_provisioned` — bail before
    # any prompts collect anything if the operator lacks access.
    _confirm_private_access_or_abort()

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

    from iblai_infra.ansible.runner import AnsibleRunner, CALL_ROLE_LABELS
    from iblai_infra.models import DeploymentType

    is_call = (
        getattr(state.config, "deployment_type", DeploymentType.SINGLE) == DeploymentType.CALL
    )

    rows = []
    if setup_config.is_resetup:
        rows.append(("Mode", "Re-setup"))
    if is_call:
        rows.append(("Mode", "Call server (LiveKit)"))
    rows.extend([
        ("Target", setup_config.target_host),
        ("SSH key", str(setup_config.ssh_private_key_path)),
        ("Domain", setup_config.base_domain),
        ("CLI ops tag", setup_config.cli_ops_release_tag),
    ])
    if not is_call:
        rows.append(("edX version", setup_config.edx_version))
    rows.extend([
        ("Env config", setup_config.env_config),
        ("AWS region", setup_config.aws_default_region),
    ])
    ui.summary_panel("Setup Summary", rows)

    import questionary

    # The private-resource access notice + access confirm fired at the
    # top of the flow (in `_run_setup_provisioned` / `_run_setup_interactive`),
    # before any prompts collected input. This is the final summary gate
    # — just confirm we're proceeding with the configured values shown
    # above.
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

    if is_call:
        # ibl_call role uses env_config; make sure it's set even if the prompt defaulted
        if not setup_config.env_config or setup_config.env_config == "single-server":
            setup_config.env_config = "call-only"
        runner = AnsibleRunner(
            state, setup_config,
            playbook="call_playbook.yml",
            role_labels=CALL_ROLE_LABELS,
        )
    else:
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
    table.add_column("Type", min_width=8, justify="center")
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

        deploy_label = getattr(s.config, "deployment_type", None)
        if deploy_label and deploy_label.value == "multi-server":
            ms = getattr(s.config, "multi_server", None)
            count = ms.app_server_count if ms else "?"
            type_display = f"multi ({count})"
        else:
            type_display = "single"

        table.add_row(
            s.name,
            type_display,
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
