"""``iblai infra smtp`` — add or change outbound email on a running environment.

SMTP is one of the things most often skipped at setup time: the mail
credentials frequently aren't available on day one. This subgroup lets an
operator come back to an environment that is already provisioned and
bootstrapped and turn email on, without re-running the whole playbook.

It re-runs only the ``smtp_config`` role, via
:meth:`AnsibleRunner.run_partial`. That role writes the ``IBL_SMTP_*`` keys
and re-renders the compose files; because the services are already running
with the old environment, they also have to be recreated before the change
takes effect — see ``restart_services``.

The role reads no credentials of its own, so this needs nothing beyond the
SSH key already recorded in the project's state and the mail settings.
"""

from __future__ import annotations

import typer

from iblai_infra import ui
from iblai_infra.models import ProjectState, SetupConfig

smtp_app = typer.Typer(
    name="smtp",
    help="Configure outbound email (SMTP) on an existing environment",
    no_args_is_help=True,
)

# Only this role runs.
SMTP_TAGS = ["smtp"]
SMTP_LABELS = {"smtp_config": "SMTP Config"}

# Recreating these is what makes a change take effect on a running box.
AFFECTED_SERVICES = "Data Manager and Open edX"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_feature_target(name: str) -> ProjectState:
    """Load ``name`` and refuse if it can't accept a post-setup change.

    Shared by the feature subgroups: they all need an environment that exists,
    still has infrastructure, and has been through setup at least once.
    """
    from iblai_infra.terraform.state import load_state

    state = load_state(name)
    if state is None:
        ui.error(f"No environment found with name: {name}")
        ui.muted("  List them with [brand]iblai infra list[/brand]")
        raise typer.Exit(1)

    if state.status == "destroyed":
        ui.error(f"'{name}' has been destroyed.")
        raise typer.Exit(1)

    if state.setup_status != "completed":
        ui.error(f"'{name}' has not completed setup yet.")
        ui.muted(
            f"  Optional features are added after setup. Run "
            f"[brand]iblai infra setup {name}[/brand] first."
        )
        raise typer.Exit(1)

    return state


def confirm_restart(services: str, *, no_restart: bool, assume_yes: bool) -> bool:
    """Ask whether to recreate the services a change depends on.

    Restarting is what makes the change live, so it defaults to yes — but on a
    running environment it means a brief outage, which the operator should be
    told about rather than have happen silently.
    """
    import questionary

    if no_restart:
        return False
    if assume_yes:
        return True

    ui.newline()
    ui.muted(f"  {services} must be recreated before this takes effect.")
    ui.muted("  They will be briefly unavailable. edX takes the longest to come back.")
    answer = questionary.confirm(
        "Restart them once the configuration is written?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    return bool(answer)


def apply_feature(
    state: ProjectState,
    config: SetupConfig,
    tags: list[str],
    labels: dict[str, str],
    description: str,
) -> bool:
    """Run the tagged role(s) against the environment. Returns success."""
    from iblai_infra.ansible.runner import AnsibleRunner

    runner = AnsibleRunner(state, config, role_labels=labels)
    if not runner.preflight():
        raise typer.Exit(1)
    runner.setup()
    return runner.run_partial(tags, description=description)


# ---------------------------------------------------------------------------
# enable
# ---------------------------------------------------------------------------

@smtp_app.command("enable")
def smtp_enable(
    name: str = typer.Argument(help="Environment name"),
    no_restart: bool = typer.Option(
        False, "--no-restart", help="Write the config but leave the services running as-is"
    ),
    assume_yes: bool = typer.Option(
        False, "--yes", "-y", help="Don't ask before restarting"
    ),
) -> None:
    """Turn on outbound email, or update the existing mail settings."""
    import questionary

    state = load_feature_target(name)

    ui.newline()
    ui.console.rule("[brand]SMTP[/brand]")
    ui.muted(f"  Configuring outbound email for [highlight]{name}[/highlight]")
    ui.newline()

    host = questionary.text(
        "SMTP host:", validate=lambda v: bool(v.strip()) or "Required",
        style=ui.PROMPT_STYLE, qmark=ui.QMARK,
    ).ask()
    if host is None:
        ui.abort()

    port = questionary.text(
        "SMTP port:", default="587",
        validate=lambda v: v.strip().isdigit() or "Must be a number",
        style=ui.PROMPT_STYLE, qmark=ui.QMARK,
    ).ask()
    if port is None:
        ui.abort()

    username = questionary.text(
        "SMTP username:", validate=lambda v: bool(v.strip()) or "Required",
        style=ui.PROMPT_STYLE, qmark=ui.QMARK,
    ).ask()
    if username is None:
        ui.abort()

    password = questionary.password(
        "SMTP password:", validate=lambda v: bool(v.strip()) or "Required",
        style=ui.PROMPT_STYLE, qmark=ui.QMARK,
    ).ask()
    if password is None:
        ui.abort()

    sender = questionary.text(
        "Sender address (From:):",
        default=f"noreply@{state.config.dns.base_domain}",
        style=ui.PROMPT_STYLE, qmark=ui.QMARK,
    ).ask()
    if sender is None:
        ui.abort()

    security = questionary.select(
        "Connection security:",
        choices=[
            questionary.Choice("STARTTLS (most providers, port 587)", value="tls"),
            questionary.Choice("SSL/TLS (port 465)", value="ssl"),
            questionary.Choice("None", value="none"),
        ],
        style=ui.PROMPT_STYLE, qmark=ui.QMARK,
    ).ask()
    if security is None:
        ui.abort()

    restart = confirm_restart(
        AFFECTED_SERVICES, no_restart=no_restart, assume_yes=assume_yes
    )

    config = SetupConfig.for_feature(
        state,
        smtp_enabled=True,
        smtp_host=host.strip(),
        smtp_port=int(port.strip()),
        smtp_username=username.strip(),
        smtp_password=password,
        smtp_sender_email=(sender or "").strip(),
        smtp_use_tls=(security == "tls"),
        smtp_use_ssl=(security == "ssl"),
        restart_services=restart,
    )

    _run_and_report(state, config, name, restart=restart, action="configured")


@smtp_app.command("enable-env")
def smtp_enable_env(
    name: str = typer.Argument(help="Environment name"),
    env_file: str = typer.Option(..., "-f", "--file", help="Path to a .env file"),
    no_restart: bool = typer.Option(False, "--no-restart", help="Skip the service restart"),
) -> None:
    """Non-interactive SMTP enable, reading the same keys as `setup-env`.

    Uses SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_SENDER_EMAIL,
    SMTP_USE_TLS and SMTP_USE_SSL, so one .env works for setup and for this.
    """
    from iblai_infra.env_utils import load_env_file, parse_bool

    state = load_feature_target(name)
    env = load_env_file(env_file)

    host = (env.get("SMTP_HOST") or "").strip()
    if not host:
        ui.error(f"SMTP_HOST is not set in {env_file}")
        raise typer.Exit(1)

    config = SetupConfig.for_feature(
        state,
        smtp_enabled=True,
        smtp_host=host,
        smtp_port=int((env.get("SMTP_PORT") or "587").strip()),
        smtp_username=(env.get("SMTP_USERNAME") or "").strip(),
        smtp_password=env.get("SMTP_PASSWORD") or "",
        smtp_sender_email=(env.get("SMTP_SENDER_EMAIL") or "").strip(),
        smtp_use_tls=parse_bool(env.get("SMTP_USE_TLS"), default=True),
        smtp_use_ssl=parse_bool(env.get("SMTP_USE_SSL"), default=False),
        restart_services=not no_restart,
    )

    _run_and_report(state, config, name, restart=not no_restart, action="configured")


@smtp_app.command("disable")
def smtp_disable(
    name: str = typer.Argument(help="Environment name"),
    assume_yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
    no_restart: bool = typer.Option(False, "--no-restart", help="Skip the service restart"),
) -> None:
    """Clear the mail settings so the platform stops sending email."""
    import questionary

    state = load_feature_target(name)

    if not assume_yes:
        ui.warning(f"This clears the SMTP settings on '{name}'. Email will stop working.")
        if not questionary.confirm(
            "Continue?", default=False, style=ui.PROMPT_STYLE, qmark=ui.QMARK
        ).ask():
            ui.abort()

    restart = confirm_restart(
        AFFECTED_SERVICES, no_restart=no_restart, assume_yes=assume_yes
    )

    # The role writes whatever it is given; blanking the values is how the
    # platform is told to stop using them.
    config = SetupConfig.for_feature(
        state,
        smtp_enabled=True,
        smtp_host="",
        smtp_username="",
        smtp_password="",
        smtp_sender_email="",
        restart_services=restart,
    )

    _run_and_report(state, config, name, restart=restart, action="cleared")


@smtp_app.command("status")
def smtp_status(
    name: str = typer.Argument(help="Environment name"),
) -> None:
    """Show the mail settings currently on the server."""
    state = load_feature_target(name)
    _print_remote_config(
        state,
        keys=["IBL_SMTP_HOST", "IBL_SMTP_PORT", "IBL_SMTP_USER", "IBL_SMTP_SENDER_MAIL"],
        title="SMTP",
        empty_hint=f"Not configured. Turn it on with [brand]iblai infra smtp enable {name}[/brand]",
    )


# ---------------------------------------------------------------------------
# Shared output
# ---------------------------------------------------------------------------

def _run_and_report(
    state: ProjectState,
    config: SetupConfig,
    name: str,
    *,
    restart: bool,
    action: str,
) -> None:
    ok = apply_feature(
        state, config, SMTP_TAGS, SMTP_LABELS, description=f"SMTP {action}"
    )
    ui.newline()
    if not ok:
        ui.error("Could not apply the SMTP configuration.")
        raise typer.Exit(1)

    ui.success(f"SMTP {action} on [highlight]{name}[/highlight].")
    if not restart:
        ui.newline()
        ui.warning("The services were not restarted, so this is not live yet.")
        ui.muted(f"  Apply it with [brand]iblai infra smtp enable {name} --yes[/brand],")
        ui.muted("  or restart Data Manager and edX on the server yourself.")
    ui.newline()


def _print_remote_config(
    state: ProjectState, keys: list[str], title: str, empty_hint: str
) -> None:
    """Read config values back off the server and print them.

    Nothing local records what an environment is configured with — the setup
    config holds secrets and is deliberately never persisted — so the server is
    the only source of truth.
    """
    from iblai_infra.ansible.runner import AnsibleRunner

    config = SetupConfig.for_feature(state)
    runner = AnsibleRunner(state, config)

    ui.newline()
    ui.console.rule(f"[brand]{title}[/brand]")

    values = runner.read_config_values(keys)
    if values is None:
        ui.error("Could not reach the server to read its configuration.")
        raise typer.Exit(1)

    if not any((v or "").strip() for v in values.values()):
        ui.muted(f"  {empty_hint}")
        ui.newline()
        return

    width = max(len(k) for k in keys)
    for key in keys:
        val = (values.get(key) or "").strip() or "[muted]not set[/muted]"
        ui.console.print(f"  {key:<{width}}  {val}")
    ui.newline()
