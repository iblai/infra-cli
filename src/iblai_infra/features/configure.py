"""``iblai infra configure`` — one entry point for the optional integrations.

The per-feature subgroups are precise and scriptable, but only if you already
know a feature exists and what it is called. An operator coming back weeks
after setup to "add email" shouldn't have to guess at `smtp`.

This lists what can be added and hands off to the same commands, so there is
one thing to remember and nothing duplicated behind it.
"""

from __future__ import annotations

import typer

from iblai_infra import ui
from iblai_infra.features._common import load_feature_target


def configure(
    name: str = typer.Argument(help="Environment name"),
) -> None:
    """Add an optional integration to an environment that is already set up."""
    import questionary

    from iblai_infra.features.llm import llm_set_key
    from iblai_infra.features.platform import platform_create
    from iblai_infra.features.smtp import smtp_enable
    from iblai_infra.features.sso import sso_google, sso_microsoft
    from iblai_infra.features.spa import spa_clone
    from iblai_infra.features.stripe import stripe_enable

    # Validate the target once, here, so an invalid name fails before the menu
    # rather than after the operator has picked something.
    load_feature_target(name)

    ui.newline()
    ui.console.rule(f"[brand]Configure {name}[/brand]")
    ui.muted("  These are all optional and can be added at any time.")
    ui.newline()

    choice = questionary.select(
        "What would you like to add?",
        choices=[
            questionary.Choice("Email (SMTP)              outbound mail", value="smtp"),
            questionary.Choice("Google SSO                sign in with Google", value="google"),
            questionary.Choice("Microsoft SSO             sign in with Microsoft", value="microsoft"),
            questionary.Choice("Stripe billing            payments", value="stripe"),
            questionary.Choice("LLM API key               mentor service credential", value="llm"),
            questionary.Choice("Tenant platform           an additional platform", value="platform"),
            questionary.Choice("Clone a SPA               a customisable copy of one", value="spa"),
            questionary.Separator(),
            questionary.Choice("Cancel", value=None),
        ],
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    if choice is None:
        ui.newline()
        ui.muted("  Nothing changed.")
        ui.newline()
        return

    # Each of these is the same function the standalone command calls, so the
    # menu can never drift from the direct form.
    if choice == "smtp":
        smtp_enable(name=name, no_restart=False, assume_yes=False)
    elif choice == "google":
        sso_google(name=name)
    elif choice == "microsoft":
        sso_microsoft(name=name, assume_yes=False)
    elif choice == "stripe":
        stripe_enable(name=name)
    elif choice == "llm":
        llm_set_key(name=name, api_key=None)
    elif choice == "platform":
        platform_create(name=name, platform_name=None)
    elif choice == "spa":
        spa_clone(name=name, source=None, new_name=None, domain=None, port=None)
