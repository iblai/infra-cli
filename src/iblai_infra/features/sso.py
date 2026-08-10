"""``iblai infra sso`` — add Google or Microsoft sign-in to a running environment.

SSO is routinely deferred at setup because the OAuth application has to be
registered in Google Workspace or Azure first, which is someone else's task
and rarely done on day one.

The two providers differ in how the setting reaches the platform, which is
why only one of them mentions a restart:

* **Google** creates an ``OAuth2ProviderConfig`` row in the LMS. That is a
  ``ConfigurationModel``, read per request, so sign-in works the moment the
  role finishes.
* **Microsoft** also patches the edX settings block in ``config.yml``, which
  is only read at boot - so its role restarts edX itself whenever the
  configuration actually changed. There is no opt-out worth offering: skipping
  the restart would leave the setting inert.
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

sso_app = typer.Typer(
    name="sso",
    help="Configure single sign-on (Google or Microsoft) on an existing environment",
    no_args_is_help=True,
)

GOOGLE_TAGS = ["google_sso"]
GOOGLE_LABELS = {"google_sso_config": "Google SSO Config"}

MICROSOFT_TAGS = ["microsoft_sso"]
MICROSOFT_LABELS = {"microsoft_sso_config": "Microsoft SSO Config"}


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

@sso_app.command("google")
def sso_google(
    name: str = typer.Argument(help="Environment name"),
) -> None:
    """Enable Google sign-in.

    Register an OAuth client in Google Cloud Console first. Its authorised
    redirect URI must be the LMS callback:
    `https://learn.<your-domain>/auth/complete/google-oauth2/`
    """
    state = load_feature_target(name)
    domain = state.config.dns.base_domain

    ui.newline()
    ui.console.rule("[brand]Google SSO[/brand]")
    ui.muted(f"  The OAuth client's authorised redirect URI must be:")
    ui.muted(f"    https://learn.{domain}/auth/complete/google-oauth2/")
    ui.newline()

    client_id = prompt_required("Google OAuth Client ID:")
    client_secret = prompt_required("Google OAuth Client Secret:", secret=True)
    organization = prompt_optional(
        "Restrict to a Google Workspace domain (blank = any account):"
    )

    config = SetupConfig.for_feature(
        state,
        google_sso_enabled=True,
        google_sso_client_id=client_id,
        google_sso_client_secret=client_secret,
        google_sso_organization=organization,
    )

    run_feature(
        state, config, tags=GOOGLE_TAGS, labels=GOOGLE_LABELS,
        name=name, what="Google SSO",
    )
    ui.muted(f"  Sign-in is live now — no restart needed.")
    ui.newline()


# ---------------------------------------------------------------------------
# Microsoft
# ---------------------------------------------------------------------------

@sso_app.command("microsoft")
def sso_microsoft(
    name: str = typer.Argument(help="Environment name"),
    assume_yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
) -> None:
    """Enable Microsoft (Azure AD) sign-in.

    Register an application in Azure first. Its redirect URI must be the LMS
    callback: `https://learn.<your-domain>/auth/complete/azuread-oauth2/`
    """
    import questionary

    state = load_feature_target(name)
    domain = state.config.dns.base_domain

    ui.newline()
    ui.console.rule("[brand]Microsoft SSO[/brand]")
    ui.muted("  The Azure application's redirect URI must be:")
    ui.muted(f"    https://learn.{domain}/auth/complete/azuread-oauth2/")
    ui.newline()

    client_id = prompt_required("Application (client) ID:")
    client_secret = prompt_required("Client secret value:", secret=True)
    tenant_id = prompt_required("Directory (tenant) ID:")
    organization = prompt_optional("Organization label (optional):")

    # Unlike Google, this one patches edX settings, which are read at boot.
    # The role restarts edX when the configuration changed - say so rather
    # than have a production LMS bounce unannounced.
    if not assume_yes:
        ui.newline()
        ui.warning("Open edX will be restarted so the new sign-in settings take effect.")
        ui.muted("  It will be briefly unavailable, and is the slowest service to return.")
        if not questionary.confirm(
            "Continue?", default=True, style=ui.PROMPT_STYLE, qmark=ui.QMARK
        ).ask():
            ui.abort()

    config = SetupConfig.for_feature(
        state,
        microsoft_sso_enabled=True,
        microsoft_sso_client_id=client_id,
        microsoft_sso_client_secret=client_secret,
        microsoft_sso_tenant_id=tenant_id,
        microsoft_sso_organization=organization,
    )

    run_feature(
        state, config, tags=MICROSOFT_TAGS, labels=MICROSOFT_LABELS,
        name=name, what="Microsoft SSO",
    )
