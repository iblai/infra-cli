"""``iblai infra smtp`` — add or change outbound email on a running environment.

SMTP is the most commonly deferred integration: the mail credentials often
aren't available on day one. This re-runs only the ``smtp_config`` role.

It is also the only feature that needs a restart. The SMTP values reach the
services as container environment variables, so writing them and re-rendering
the compose files doesn't touch containers that are already running - they
have to be recreated.
"""

from __future__ import annotations

import typer

from iblai_infra import ui
from iblai_infra.features._common import (
    confirm_restart,
    load_feature_target,
    print_remote_config,
    prompt_optional,
    prompt_required,
    run_feature,
)
from iblai_infra.models import SetupConfig

smtp_app = typer.Typer(
    name="smtp",
    help="Configure outbound email (SMTP) on an existing environment",
    no_args_is_help=True,
)

SMTP_TAGS = ["smtp"]
SMTP_LABELS = {"smtp_config": "SMTP Config"}
AFFECTED_SERVICES = "Data Manager and Open edX"

STATUS_KEYS = ["IBL_SMTP_HOST", "IBL_SMTP_PORT", "IBL_SMTP_USER", "IBL_SMTP_SENDER_MAIL"]


@smtp_app.command("enable")
def smtp_enable(
    name: str = typer.Argument(help="Environment name"),
    no_restart: bool = typer.Option(
        False, "--no-restart", help="Write the config but leave the services running as-is"
    ),
    assume_yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before restarting"),
) -> None:
    """Turn on outbound email, or update the existing mail settings."""
    import questionary

    state = load_feature_target(name)

    ui.newline()
    ui.console.rule("[brand]SMTP[/brand]")
    ui.muted(f"  Configuring outbound email for [highlight]{name}[/highlight]")
    ui.newline()

    host = prompt_required("SMTP host:")
    port = prompt_required(
        "SMTP port:", default="587",
        validate=lambda v: v.strip().isdigit() or "Must be a number",
    )
    username = prompt_required("SMTP username:")
    password = prompt_required("SMTP password:", secret=True)
    sender = prompt_optional(
        "Sender address (From:):", default=f"noreply@{state.config.dns.base_domain}"
    )

    security = questionary.select(
        "Connection security:",
        choices=[
            questionary.Choice("STARTTLS (most providers, port 587)", value="tls"),
            questionary.Choice("SSL/TLS (port 465)", value="ssl"),
            questionary.Choice("None", value="none"),
        ],
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if security is None:
        ui.abort()

    restart = confirm_restart(AFFECTED_SERVICES, no_restart=no_restart, assume_yes=assume_yes)

    config = SetupConfig.for_feature(
        state,
        smtp_enabled=True,
        smtp_host=host,
        smtp_port=int(port),
        smtp_username=username,
        smtp_password=password,
        smtp_sender_email=sender,
        smtp_use_tls=(security == "tls"),
        smtp_use_ssl=(security == "ssl"),
        restart_services=restart,
    )

    run_feature(
        state, config, tags=SMTP_TAGS, labels=SMTP_LABELS, name=name, what="SMTP",
        live_now=restart,
        not_live_hint=f"Apply it with [brand]iblai infra smtp enable {name} --yes[/brand], "
                      "or restart Data Manager and edX on the server yourself.",
    )


@smtp_app.command("enable-env")
def smtp_enable_env(
    name: str = typer.Argument(help="Environment name"),
    env_file: str = typer.Option(..., "-f", "--file", help="Path to a .env file"),
    no_restart: bool = typer.Option(False, "--no-restart", help="Skip the service restart"),
) -> None:
    """Non-interactive SMTP enable, reading the same keys as `setup-env`."""
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

    run_feature(
        state, config, tags=SMTP_TAGS, labels=SMTP_LABELS, name=name, what="SMTP",
        live_now=not no_restart,
    )


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

    restart = confirm_restart(AFFECTED_SERVICES, no_restart=no_restart, assume_yes=assume_yes)

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

    run_feature(
        state, config, tags=SMTP_TAGS, labels=SMTP_LABELS, name=name, what="SMTP",
        action="cleared", live_now=restart,
    )


@smtp_app.command("status")
def smtp_status(name: str = typer.Argument(help="Environment name")) -> None:
    """Show the mail settings currently on the server."""
    state = load_feature_target(name)
    print_remote_config(
        state,
        keys=STATUS_KEYS,
        title="SMTP",
        empty_hint=f"Not configured. Turn it on with [brand]iblai infra smtp enable {name}[/brand]",
    )
