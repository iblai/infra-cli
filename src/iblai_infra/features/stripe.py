"""``iblai infra stripe`` — add billing to a running environment.

The role writes database rows the platform reads per request, so billing is
live as soon as it finishes; nothing needs restarting.

Stripe has the largest set of values of any optional integration, and four of
them are secrets. They are prompted individually rather than pasted as a blob
so it is obvious which is which - mixing up the publishable and secret keys is
the easy mistake here.
"""

from __future__ import annotations

import typer

from iblai_infra import ui
from iblai_infra.features._common import (
    load_feature_target,
    prompt_optional,
    prompt_required,
    run_feature,
)
from iblai_infra.models import SetupConfig

stripe_app = typer.Typer(
    name="stripe",
    help="Configure Stripe billing on an existing environment",
    no_args_is_help=True,
)

STRIPE_TAGS = ["stripe"]
STRIPE_LABELS = {"stripe_config": "Stripe Config"}


@stripe_app.command("enable")
def stripe_enable(
    name: str = typer.Argument(help="Environment name"),
) -> None:
    """Turn on Stripe billing, or update the existing keys."""
    import questionary

    state = load_feature_target(name)

    ui.newline()
    ui.console.rule("[brand]Stripe[/brand]")
    ui.muted(f"  Configuring billing for [highlight]{name}[/highlight]")
    ui.newline()

    mode = questionary.select(
        "Which Stripe mode?",
        choices=[
            questionary.Choice("Test — no real charges", value="test"),
            questionary.Choice("Live — real charges", value="live"),
        ],
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if mode is None:
        ui.abort()

    if mode == "live":
        ui.newline()
        ui.warning("Live mode processes real payments against real cards.")

    secret_key = prompt_required("Secret key (sk_...):", secret=True)
    pub_key = prompt_required("Publishable key (pk_...):")
    pricing_table = prompt_optional("Pricing table ID (optional):")
    pricing_table_returning = prompt_optional(
        "Pricing table ID for returning customers (optional):"
    )
    webhook_secret = prompt_optional("Webhook signing secret (whsec_..., optional):")
    connect_webhook_secret = prompt_optional(
        "Connect webhook signing secret (optional):"
    )

    config = SetupConfig.for_feature(
        state,
        stripe_enabled=True,
        stripe_mode=mode,
        stripe_secret_key=secret_key,
        stripe_pub_key=pub_key,
        stripe_pricing_table_id=pricing_table,
        stripe_pricing_table_id_returning=pricing_table_returning,
        stripe_webhook_secret=webhook_secret,
        stripe_connect_webhook_secret=connect_webhook_secret,
    )

    run_feature(
        state, config, tags=STRIPE_TAGS, labels=STRIPE_LABELS,
        name=name, what=f"Stripe billing ({mode} mode)",
    )
    ui.muted("  Billing is live now — no restart needed.")
    ui.newline()


@stripe_app.command("enable-env")
def stripe_enable_env(
    name: str = typer.Argument(help="Environment name"),
    env_file: str = typer.Option(..., "-f", "--file", help="Path to a .env file"),
) -> None:
    """Non-interactive Stripe enable, reading the same keys as `setup-env`."""
    from iblai_infra.env_utils import load_env_file

    state = load_feature_target(name)
    env = load_env_file(env_file)

    secret_key = (env.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret_key:
        ui.error(f"STRIPE_SECRET_KEY is not set in {env_file}")
        raise typer.Exit(1)

    mode = (env.get("STRIPE_MODE") or "test").strip().lower()
    if mode not in ("test", "live"):
        ui.error(f"STRIPE_MODE must be 'test' or 'live', got '{mode}'")
        raise typer.Exit(1)

    config = SetupConfig.for_feature(
        state,
        stripe_enabled=True,
        stripe_mode=mode,
        stripe_secret_key=secret_key,
        stripe_pub_key=(env.get("STRIPE_PUB_KEY") or "").strip(),
        stripe_pricing_table_id=(env.get("STRIPE_PRICING_TABLE_ID") or "").strip(),
        stripe_pricing_table_id_returning=(
            env.get("STRIPE_PRICING_TABLE_ID_RETURNING") or ""
        ).strip(),
        stripe_webhook_secret=(env.get("STRIPE_WEBHOOK_SECRET") or "").strip(),
        stripe_connect_webhook_secret=(
            env.get("STRIPE_CONNECT_WEBHOOK_SECRET") or ""
        ).strip(),
    )

    run_feature(
        state, config, tags=STRIPE_TAGS, labels=STRIPE_LABELS,
        name=name, what=f"Stripe billing ({mode} mode)",
    )
