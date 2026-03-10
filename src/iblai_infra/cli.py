"""CLI entry point — `iblai infra <command>` structure.

Root:  iblai --version | --help
Group: iblai infra provision | setup | destroy | status | list
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from iblai_infra import __version__, ui
from iblai_infra.terraform.state import list_all_states, load_session, load_state, save_session

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
        ("iblai infra setup <name>", "Bootstrap a provisioned VM with the IBL platform"),
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
            questionary.Choice("Set up platform on provisioned VM", value="setup"),
            questionary.Choice("Check AWS permissions", value="permissions"),
            questionary.Choice("List managed environments", value="list"),
            questionary.Choice("Show required IAM policy", value="policy"),
            questionary.Choice("Switch AWS credentials", value="auth"),
            questionary.Choice("Exit", value="exit"),
        ],
        style=ui.PROMPT_STYLE,
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
    elif action == "setup":
        _interactive_setup()
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
def setup(
    name: str = typer.Argument(help="Project name to set up"),
) -> None:
    """Bootstrap a provisioned VM with the IBL platform."""
    _run_setup(name)


def _interactive_setup() -> None:
    """Launch setup from the landing menu — prompts for project name."""
    import questionary

    states = list_all_states()
    eligible = [s for s in states if s.status == "created"]

    if not eligible:
        ui.info("No provisioned environments found to set up.")
        ui.muted("Run [brand]iblai infra provision[/brand] first.")
        ui.newline()
        return

    if len(eligible) == 1:
        _run_setup(eligible[0].name)
        return

    choices = [
        questionary.Choice(
            f"{s.name} ({s.config.dns.base_domain})",
            value=s.name,
        )
        for s in eligible
    ]
    name = questionary.select(
        "Which environment?",
        choices=choices,
        style=ui.PROMPT_STYLE,
    ).ask()
    if name is None:
        return

    _run_setup(name)


def _run_setup(name: str) -> None:
    """Core setup logic shared by the command and the interactive menu."""
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
        ).ask()
        if not rerun:
            raise typer.Exit(0)

    import shutil

    from iblai_infra.ansible.runner import AnsibleRunner
    from iblai_infra.prompts.setup import prompt_setup

    # Pre-flight: check ansible is installed
    if shutil.which("ansible-playbook") is None:
        ui.error("ansible-playbook not found")
        ui.newline()
        ui.info("Install with: [highlight]pip install ansible-core[/highlight]")
        ui.muted(f"Then re-run: [brand]iblai infra setup {name}[/brand]")
        ui.newline()
        raise typer.Exit(1)

    # Collect setup variables
    try:
        setup_config = prompt_setup(state)
    except KeyboardInterrupt:
        ui.newline()
        ui.abort("Interrupted.")

    # Review summary
    rows = [
        ("Target", setup_config.target_host),
        ("SSH key", str(setup_config.ssh_private_key_path)),
        ("Domain", setup_config.base_domain),
        ("edX version", setup_config.edx_version),
        ("Env config", setup_config.env_config),
        ("AWS region", setup_config.aws_default_region),
    ]
    ui.summary_panel("Setup Summary", rows)

    import questionary

    confirm = questionary.confirm(
        "Proceed with setup?",
        default=True,
        style=ui.PROMPT_STYLE,
    ).ask()
    if not confirm:
        ui.abort("Cancelled.")

    # Run ansible
    runner = AnsibleRunner(state, setup_config)

    if not runner.preflight():
        raise typer.Exit(1)

    runner.setup()

    try:
        success = runner.run()
    except KeyboardInterrupt:
        from datetime import datetime, timezone

        ui.newline()
        state.setup_status = "failed"
        state.updated_at = datetime.now(timezone.utc)
        from iblai_infra.terraform.state import save_state
        save_state(state)
        ui.abort("Interrupted. Re-run with: iblai infra setup " + name)

    if success:
        ui.newline()
        ip = setup_config.target_host
        key_flag = f"-i {setup_config.ssh_private_key_path} " if setup_config.ssh_private_key_path else ""
        ui.success(f"Platform bootstrapped on [highlight]{ip}[/highlight]")
        ui.info(f"SSH: [highlight]ssh {key_flag}ubuntu@{ip}[/highlight]")
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
    ).ask()

    if not confirm:
        ui.abort("Cancelled.")

    # Double confirm for production
    if state.config.environment.value == "prod":
        confirm2 = questionary.text(
            f'Type "{name}" to confirm production destruction:',
            style=ui.PROMPT_STYLE,
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
