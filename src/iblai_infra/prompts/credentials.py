"""Step 1 — AWS Authentication wizard."""

from __future__ import annotations

from pathlib import Path

import questionary
from rich.status import Status

from iblai_infra import ui
from iblai_infra.models import (
    AWSCredentials,
    AWS_REGIONS,
    AuthMethod,
    CloudProvider,
    GCP_REGIONS,
    GCPAuthMethod,
    GCPCredentials,
)
from iblai_infra.providers.aws import (
    detect_current_ip,
    has_env_credentials,
    list_profiles,
    validate_credentials,
)

TOTAL_STEPS = 5


def prompt_credentials(show_step: bool = True) -> AWSCredentials:
    """Run the full AWS credentials wizard and return validated credentials."""

    if show_step:
        ui.step_header(1, TOTAL_STEPS, "AWS Authentication")
    else:
        ui.newline()
        ui.info("[highlight]AWS Authentication[/highlight]")
        ui.newline()

    # ----- auth method -----
    choices = []
    profiles = list_profiles()
    if profiles:
        choices.append(
            questionary.Choice(
                title=f"AWS Profile (from ~/.aws/config — {len(profiles)} found)",
                value=AuthMethod.PROFILE,
            )
        )
    env_available = has_env_credentials()
    if env_available:
        choices.append(
            questionary.Choice(
                title="Environment Variables (AWS_ACCESS_KEY_ID detected)",
                value=AuthMethod.ENVIRONMENT,
            )
        )
    choices.append(
        questionary.Choice(
            title="Access Key + Secret Key (enter manually)",
            value=AuthMethod.ACCESS_KEY,
        )
    )

    method: AuthMethod = questionary.select(
        "How would you like to authenticate?",
        choices=choices,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    if method is None:
        ui.abort()

    # ----- collect credentials based on method -----
    profile = None
    access_key_id = None
    secret_access_key = None

    if method == AuthMethod.PROFILE:
        profile = questionary.autocomplete(
            "Select AWS profile (type to filter):",
            choices=profiles,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
            validate=lambda v: v in profiles or "Select a valid profile from the list",
        ).ask()
        if profile is None:
            ui.abort()

    elif method == AuthMethod.ACCESS_KEY:
        access_key_id = questionary.text(
            "AWS Access Key ID:",
            validate=lambda v: len(v.strip()) >= 16 or "Invalid access key",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if access_key_id is None:
            ui.abort()

        secret_access_key = questionary.password(
            "AWS Secret Access Key:",
            validate=lambda v: len(v.strip()) >= 16 or "Invalid secret key",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if secret_access_key is None:
            ui.abort()

    # ----- region -----
    valid_regions = list(AWS_REGIONS.keys())
    region = questionary.autocomplete(
        "AWS Region (type to filter):",
        choices=valid_regions,
        default="us-east-1",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
        validate=lambda v: v in valid_regions or "Select a valid region from the list",
    ).ask()
    if region is None:
        ui.abort()

    # ----- validate -----
    creds = AWSCredentials(
        method=method,
        profile=profile,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
    )

    with Status("[info]Validating credentials...[/info]", console=ui.console):
        try:
            identity = validate_credentials(creds)
            creds.account_id = identity.account_id
            creds.arn = identity.arn
        except ValueError as e:
            ui.error(str(e))
            ui.abort("Could not authenticate with AWS. Please check your credentials.")

    ui.success(f"Authenticated as [highlight]{identity.arn}[/highlight]")
    ui.muted(f"Account: {identity.account_id}")

    # Save session for reuse across commands
    from iblai_infra.terraform.state import save_session
    save_session(creds)

    return creds


# ---------------------------------------------------------------------------
# Provider selection + GCP authentication
# ---------------------------------------------------------------------------

def prompt_provider() -> CloudProvider:
    """Ask which cloud to provision on. The wizard's first step."""
    ui.newline()
    choice = questionary.select(
        "Which cloud provider?",
        choices=[
            questionary.Choice("AWS  — single-server, multi-server, or call-server", value=CloudProvider.AWS),
            questionary.Choice("GCP  — single-server", value=CloudProvider.GCP),
        ],
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if choice is None:
        ui.abort()
    return choice


def prompt_gcp_credentials(show_step: bool = True) -> GCPCredentials:
    """Collect + validate GCP credentials (project, region/zone, ADC or SA key)."""
    from iblai_infra.providers import gcp as gcp_provider

    if not gcp_provider.is_available():
        ui.error("GCP support needs extra dependencies that aren't installed.")
        ui.muted("  Install them:  uv sync --extra gcp   (or: pip install 'iblai-infra[gcp]')")
        ui.abort()

    if show_step:
        ui.step_header(1, TOTAL_STEPS, "GCP Authentication")
    else:
        ui.newline()
        ui.info("[highlight]GCP Authentication[/highlight]")
        ui.newline()

    project_id = questionary.text(
        "GCP project ID (not the display name):",
        validate=lambda v: bool(v.strip()) or "Required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if project_id is None:
        ui.abort()
    project_id = project_id.strip()

    regions = list(GCP_REGIONS.keys())
    region = questionary.autocomplete(
        "GCP region (type to filter):",
        choices=regions,
        default="us-central1",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
        validate=lambda v: v in regions or "Select a valid region from the list",
    ).ask()
    if region is None:
        ui.abort()
    region = region.strip()

    zone = questionary.text(
        "GCP zone (must be within the region):",
        default=f"{region}-a",
        validate=lambda v: v.strip().startswith(region) or f"Zone must be within {region}",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if zone is None:
        ui.abort()

    auth_method = questionary.select(
        "How would you like to authenticate?",
        choices=[
            questionary.Choice(
                "Application Default Credentials (gcloud auth application-default login)",
                value=GCPAuthMethod.ADC,
            ),
            questionary.Choice("Service-account key file (JSON)", value=GCPAuthMethod.SERVICE_ACCOUNT_KEY),
        ],
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if auth_method is None:
        ui.abort()

    credentials_file = None
    if auth_method == GCPAuthMethod.SERVICE_ACCOUNT_KEY:
        key_path = questionary.path(
            "Path to service-account key JSON:",
            validate=lambda p: Path(p).expanduser().exists() or "File not found",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if key_path is None:
            ui.abort()
        credentials_file = str(Path(key_path).expanduser())

    creds = GCPCredentials(
        method=auth_method,
        project_id=project_id,
        region=region,
        zone=zone.strip(),
        credentials_file=credentials_file,
    )

    from iblai_infra.providers import gcp as gcp_provider

    with Status("[info]Validating credentials...[/info]", console=ui.console):
        try:
            identity = gcp_provider.validate_credentials(creds)
            creds.account = identity.account
        except ValueError as e:
            ui.error(str(e))
            ui.abort("Could not authenticate with GCP. Please check your credentials.")

    ui.success(f"Authenticated — project [highlight]{creds.project_id}[/highlight]")
    if creds.account and creds.account != "(unknown)":
        ui.muted(f"Account: {creds.account}")

    return creds
