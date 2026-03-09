"""Step 1 — AWS Authentication wizard."""

from __future__ import annotations

import questionary
from rich.status import Status

from iblai_infra import ui
from iblai_infra.models import AWSCredentials, AWS_REGIONS, AuthMethod
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
            validate=lambda v: v in profiles or "Select a valid profile from the list",
        ).ask()
        if profile is None:
            ui.abort()

    elif method == AuthMethod.ACCESS_KEY:
        access_key_id = questionary.text(
            "AWS Access Key ID:",
            validate=lambda v: len(v.strip()) >= 16 or "Invalid access key",
            style=ui.PROMPT_STYLE,
        ).ask()
        if access_key_id is None:
            ui.abort()

        secret_access_key = questionary.password(
            "AWS Secret Access Key:",
            validate=lambda v: len(v.strip()) >= 16 or "Invalid secret key",
            style=ui.PROMPT_STYLE,
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

    return creds
