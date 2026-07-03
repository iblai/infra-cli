"""Setup prompts — collect variables for Ansible VM bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

import questionary

from iblai_infra import ui
from iblai_infra.env_utils import resolve_pinned_cli_ops_tag
from iblai_infra.models import (
    ProjectState,
    RESERVED_ADMIN_USERNAMES,
    RESERVED_PLATFORM_NAMES,
    SetupConfig,
    SSHKeyMethod,
    is_reserved_admin_username,
    is_reserved_platform_name,
    parse_repo_path,
)


def _validate_admin_username(value: str) -> bool | str:
    """questionary-compatible validator. Returns True or an error string."""
    s = (value or "").strip()
    if not s:
        return "Admin username is required"
    if is_reserved_admin_username(s):
        reserved = ", ".join(sorted(RESERVED_ADMIN_USERNAMES))
        return f"'{s}' is reserved for system use. Reserved: {reserved}"
    return True


def _validate_tenant_platform_name(value: str) -> bool | str:
    """questionary-compatible validator. Blank is accepted (resolves to
    `main` implicitly downstream). Explicit `main` is rejected so the
    operator can't co-opt the system default tenant.
    """
    s = (value or "").strip().lower()
    if not s:
        return True
    if is_reserved_platform_name(s):
        reserved = ", ".join(sorted(RESERVED_PLATFORM_NAMES))
        return (
            f"'{s}' is reserved for the system default tenant. "
            f"Leave blank to use the default, or pick a different name. "
            f"Reserved: {reserved}"
        )
    return True

SETUP_STEPS = 3
BOOTSTRAP_STEPS = 4
RESETUP_STEPS = 3


# ---------------------------------------------------------------------------
# SSH key resolution
# ---------------------------------------------------------------------------

def _resolve_ssh_key(state: ProjectState) -> Path | None:
    """Try to auto-resolve the SSH private key from the project state.

    Returns the private key path if found, None if the user must provide it.
    """
    ssh = state.config.ssh

    # GENERATE — private key path is stored in state
    if ssh.method == SSHKeyMethod.GENERATE and ssh.private_key_path:
        path = Path(ssh.private_key_path)
        if path.exists():
            return path
        # Key file was moved or deleted
        return None

    # EXISTING_FILE — no private key stored, but we can't guess
    # AWS_KEYPAIR — only the name is known, no local key path
    return None


def _prompt_ssh_key_path() -> Path:
    """Ask the user for the SSH private key path."""
    key_path = questionary.path(
        "Path to SSH private key:",
        validate=lambda p: (
            Path(p).expanduser().exists() or "File not found"
        ),
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if key_path is None:
        ui.abort()
    return Path(key_path).expanduser()


def validate_key_permissions(path: Path) -> bool:
    """Check that the private key has restrictive permissions."""
    try:
        mode = path.stat().st_mode & 0o777
        if mode > 0o600:
            ui.warning(
                f"SSH key has permissions {oct(mode)} — fixing to 0600"
            )
            os.chmod(path, 0o600)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Shared platform config + credentials prompts
# ---------------------------------------------------------------------------

def _prompt_platform_config(
    step: int,
    total: int,
    base_domain: str | None = None,
) -> dict:
    """Collect platform configuration. Returns a dict of config values.

    If base_domain is provided, it's used as-is (from Terraform state).
    Otherwise, it's prompted interactively.
    """
    ui.step_header(step, total, "Platform Configuration")

    if base_domain is None:
        base_domain = questionary.text(
            "Base domain (e.g. myplatform.example.com):",
            validate=lambda v: len(v.strip()) > 0 or "Required",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if base_domain is None:
            ui.abort()
        base_domain = base_domain.strip()

    ui.success(f"Domain: [highlight]{base_domain}[/highlight]")

    # Platform name — drives the SSO ansible roles (backend_name =
    # `<platform_name>-oauth2`, other_settings.platform_key) AND the
    # ibl_tenant_platform role (launches a Platform + admin via
    # run_launch_steps when value != 'main'). Blank input resolves to
    # 'main' implicitly (the system default tenant the platform itself
    # creates). Operators can't pick 'main' explicitly — it's reserved.
    platform_name = questionary.text(
        "Tenant platform name (leave blank for default 'main', no tenant launch):",
        default="",
        validate=_validate_tenant_platform_name,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if platform_name is None:
        ui.abort()
    platform_name = platform_name.strip().lower() or "main"
    if platform_name == "main":
        ui.success("Platform: [highlight]main[/highlight] (default, no tenant launch)")
    else:
        ui.success(f"Tenant platform: [highlight]{platform_name}[/highlight] (will be launched)")

    edx_version = "sumac"
    ui.success(f"Open edX version: [highlight]Sumac[/highlight]")

    env_config = "single-server"
    ui.success(f"Server type: [highlight]Single Server[/highlight]")

    # One version question: the prod-images release. iblai-cli-ops is
    # resolved from prod-images' [tool.uv.sources] pin after the GitHub
    # token is collected (see _resolve_cli_ops_release_tag).
    prod_images_tag = questionary.text(
        "iblai-prod-images release tag:",
        default="main",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if prod_images_tag is None:
        ui.abort()
    prod_images_tag = prod_images_tag.strip() or "main"
    ui.success(f"iblai-prod-images release: [highlight]{prod_images_tag}[/highlight]")

    enable_ai = questionary.confirm(
        "Enable AI features for DM?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enable_ai is None:
        ui.abort()
    if enable_ai:
        ui.success("AI features: [highlight]Enabled[/highlight]")
    else:
        ui.success("AI features: [highlight]Disabled[/highlight]")

    create_playwright_platforms = questionary.confirm(
        "Create Playwright test platforms? (8 spa-tests-* tenants + 4 browser superusers + 1 student)",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if create_playwright_platforms is None:
        ui.abort()
    if create_playwright_platforms:
        ui.success("Playwright test platforms: [highlight]Yes[/highlight]")
    else:
        ui.success("Playwright test platforms: [highlight]Skip[/highlight]")

    smtp_fields = _prompt_smtp_config()
    stripe_fields = _prompt_stripe_config()
    google_sso_fields = _prompt_google_sso_config()
    microsoft_sso_fields = _prompt_microsoft_sso_config()

    return {
        "base_domain": base_domain,
        "platform_name": platform_name,
        "edx_version": edx_version,
        "env_config": env_config,
        "prod_images_tag": prod_images_tag,
        "enable_ai": enable_ai,
        "create_playwright_platforms": create_playwright_platforms,
        **smtp_fields,
        **stripe_fields,
        **google_sso_fields,
        **microsoft_sso_fields,
    }


def _resolve_cli_ops_release_tag(cred: dict, prod_images_tag: str) -> str:
    """Resolve the iblai-cli-ops tag from the prod-images pyproject pin.

    prod-images pins ibl-cli via [tool.uv.sources] (rev = "<tag>"), but uv
    ignores that table on git-URL installs, so the Ansible role must install
    iblai-cli-ops at an explicit tag. The correct tag is the pin — fetch it
    with the operator's PAT. Falls back to a prompt when it can't be read
    (fork without a pin, monorepo path-pin, network/scopes).
    """
    from rich.status import Status

    repo, subdir = parse_repo_path(cred["prod_images_repo"])
    with Status("  [info]Resolving iblai-cli-ops pin...[/info]", console=ui.console):
        tag = resolve_pinned_cli_ops_tag(
            cred["git_access_token"],
            cred["github_org"],
            repo,
            prod_images_tag,
            subdir=subdir,
        )
    if tag:
        ui.success(
            f"iblai-cli-ops: [highlight]{tag}[/highlight] "
            f"(pinned by {repo}@{prod_images_tag})"
        )
        return tag

    ui.warning(
        f"Could not read the iblai-cli-ops pin from {repo}@{prod_images_tag} — "
        "enter the tag manually."
    )
    manual = questionary.text(
        "iblai-cli-ops release tag:",
        default="main",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if manual is None:
        ui.abort()
    manual = manual.strip() or "main"
    ui.success(f"iblai-cli-ops release: [highlight]{manual}[/highlight]")
    return manual


def _prompt_smtp_config() -> dict:
    """Optionally collect SMTP credentials for outbound email.

    Default is "skip" — when answered "no" all eight SMTP fields stay at
    their model defaults and the ansible role no-ops. When answered "yes",
    we gather the seven settings that map to the IBL_SMTP_* / IBL_SMTP_SYSTEM_*
    keys at the root of /ibl/config.yml. Password is collected via
    `questionary.password` (no echo); none of these values are persisted
    locally — they ride extra_vars to ansible at run time only.
    """
    enabled = questionary.confirm(
        "Configure SMTP for outbound email (magic-link tests, system mails)?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enabled is None:
        ui.abort()
    if not enabled:
        ui.success("SMTP: [highlight]Skip[/highlight]")
        return {"smtp_enabled": False}

    smtp_host = questionary.text(
        "SMTP host (e.g. email-smtp.us-east-1.amazonaws.com):",
        validate=lambda v: bool(v.strip()) or "Host is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if smtp_host is None:
        ui.abort()
    smtp_host = smtp_host.strip()

    smtp_port_str = questionary.text(
        "SMTP port:",
        default="587",
        validate=lambda v: v.strip().isdigit() or "Port must be an integer",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if smtp_port_str is None:
        ui.abort()
    smtp_port = int(smtp_port_str.strip())

    smtp_username = questionary.text(
        "SMTP username:",
        validate=lambda v: bool(v.strip()) or "Username is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if smtp_username is None:
        ui.abort()
    smtp_username = smtp_username.strip()

    smtp_password = questionary.password(
        "SMTP password:",
        validate=lambda v: bool(v) or "Password is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if smtp_password is None:
        ui.abort()

    smtp_sender_email = questionary.text(
        "SMTP sender email (e.g. noreply@example.com):",
        validate=lambda v: "@" in v or "Must be a valid email",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if smtp_sender_email is None:
        ui.abort()
    smtp_sender_email = smtp_sender_email.strip()

    smtp_use_tls = questionary.confirm(
        "Use STARTTLS (port 587)?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if smtp_use_tls is None:
        ui.abort()

    smtp_use_ssl = questionary.confirm(
        "Use SMTPS / implicit SSL (port 465)?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if smtp_use_ssl is None:
        ui.abort()

    ui.success(f"SMTP: [highlight]{smtp_username}@{smtp_host}:{smtp_port}[/highlight]")
    return {
        "smtp_enabled": True,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
        "smtp_sender_email": smtp_sender_email,
        "smtp_use_tls": smtp_use_tls,
        "smtp_use_ssl": smtp_use_ssl,
    }


def _prompt_stripe_config() -> dict:
    """Optionally collect Stripe billing credentials.

    Default is "skip" — when answered "no" all eight stripe_* fields stay at
    their model defaults and the ansible role no-ops. When answered "yes",
    we gather the values that map to the StripeAPIKey row on the 'main'
    platform plus the GlobalConfiguration['IBL_CURRENT_STRIPE_MODE'] entry.
    Secret-shaped fields use `questionary.password` (no echo); none of these
    values are persisted locally — they ride extra_vars to ansible at run
    time only.
    """
    enabled = questionary.confirm(
        "Configure Stripe billing? (creates StripeAPIKey + IBL_CURRENT_STRIPE_MODE)",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enabled is None:
        ui.abort()
    if not enabled:
        ui.success("Stripe: [highlight]Skip[/highlight]")
        return {"stripe_enabled": False}

    stripe_mode = questionary.select(
        "Stripe mode:",
        choices=["test", "live"],
        default="test",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if stripe_mode is None:
        ui.abort()

    stripe_secret_key = questionary.password(
        f"Stripe secret key (sk_{stripe_mode}_...):",
        validate=lambda v: v.startswith(("sk_test_", "sk_live_")) or "Must start with sk_test_ or sk_live_",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if stripe_secret_key is None:
        ui.abort()

    stripe_pub_key = questionary.password(
        f"Stripe publishable key (pk_{stripe_mode}_...):",
        validate=lambda v: v.startswith(("pk_test_", "pk_live_")) or "Must start with pk_test_ or pk_live_",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if stripe_pub_key is None:
        ui.abort()

    stripe_pricing_table_id = questionary.text(
        "Stripe pricing table id (prctbl_..., blank to skip):",
        default="",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if stripe_pricing_table_id is None:
        ui.abort()
    stripe_pricing_table_id = stripe_pricing_table_id.strip()

    stripe_pricing_table_id_returning = questionary.text(
        "Stripe pricing table id for returning users (blank to skip):",
        default="",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if stripe_pricing_table_id_returning is None:
        ui.abort()
    stripe_pricing_table_id_returning = stripe_pricing_table_id_returning.strip()

    stripe_webhook_secret = questionary.password(
        "Stripe webhook signing secret (whsec_..., blank to skip):",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if stripe_webhook_secret is None:
        ui.abort()

    stripe_connect_webhook_secret = questionary.password(
        "Stripe Connect webhook signing secret (whsec_..., blank to skip):",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if stripe_connect_webhook_secret is None:
        ui.abort()

    ui.success(f"Stripe: [highlight]{stripe_mode}[/highlight] mode")
    return {
        "stripe_enabled": True,
        "stripe_mode": stripe_mode,
        "stripe_secret_key": stripe_secret_key,
        "stripe_pub_key": stripe_pub_key,
        "stripe_pricing_table_id": stripe_pricing_table_id,
        "stripe_pricing_table_id_returning": stripe_pricing_table_id_returning,
        "stripe_webhook_secret": stripe_webhook_secret,
        "stripe_connect_webhook_secret": stripe_connect_webhook_secret,
    }


def _prompt_google_sso_config() -> dict:
    """Optionally collect Google SSO credentials.

    Default is "skip" — when answered "no" all four google_sso_* fields stay
    at their model defaults and the ansible role no-ops. When answered "yes",
    we gather the OAuth Client ID/Secret pair (from the operator's Google
    Cloud Console) plus an optional organization slug. The role then writes
    a Django `OAuth2ProviderConfig` row on the LMS for the `google-oauth2`
    python-social-auth backend, bound to `learn.<base_domain>`. Client
    secret is collected via `questionary.password` (no echo); none of these
    values are persisted locally — they ride extra_vars to ansible at run
    time only.
    """
    enabled = questionary.confirm(
        "Configure Google SSO? (creates an OAuth2ProviderConfig in the LMS)",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enabled is None:
        ui.abort()
    if not enabled:
        ui.success("Google SSO: [highlight]Skip[/highlight]")
        return {"google_sso_enabled": False}

    client_id = questionary.text(
        "Google OAuth Client ID:",
        validate=lambda v: bool(v.strip()) or "Client ID is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if client_id is None:
        ui.abort()
    client_id = client_id.strip()

    client_secret = questionary.password(
        "Google OAuth Client Secret:",
        validate=lambda v: bool(v) or "Client Secret is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if client_secret is None:
        ui.abort()

    organization = questionary.text(
        "Organization short name (optional, leave blank to skip):",
        default="",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if organization is None:
        ui.abort()
    organization = organization.strip()

    ui.success("Google SSO: [highlight]Enabled[/highlight]")
    return {
        "google_sso_enabled": True,
        "google_sso_client_id": client_id,
        "google_sso_client_secret": client_secret,
        "google_sso_organization": organization,
    }


def _prompt_microsoft_sso_config() -> dict:
    """Optionally collect Microsoft (Azure AD) SSO credentials.

    Default is "skip" — when answered "no" all five microsoft_sso_* fields
    stay at their model defaults and the ansible role no-ops. When
    answered "yes", we gather the Azure AD Application (Client) ID,
    Client Secret value, and Tenant ID, plus an optional organization
    short_name. The role then writes a Django `OAuth2ProviderConfig` row
    on the LMS for the `azuread-oauth2` slug (with `backend_name` derived
    from the operator's `platform_name`) AND patches
    `IBL_EDX.IBL_EDX_BASE_OAUTH_SSO_BACKEND` in `/ibl/config.yml`. After
    config save the role restarts edX so the new Django settings take
    effect. Client secret is collected via `questionary.password` (no
    echo); none of these values are persisted locally — they ride
    extra_vars to ansible at run time only.
    """
    enabled = questionary.confirm(
        "Configure Microsoft (Azure AD) SSO? (creates an OAuth2ProviderConfig + IBL_EDX_BASE_OAUTH_SSO_BACKEND block)",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enabled is None:
        ui.abort()
    if not enabled:
        ui.success("Microsoft SSO: [highlight]Skip[/highlight]")
        return {"microsoft_sso_enabled": False}

    client_id = questionary.text(
        "Microsoft Application (Client) ID:",
        validate=lambda v: bool(v.strip()) or "Application (Client) ID is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if client_id is None:
        ui.abort()
    client_id = client_id.strip()

    client_secret = questionary.password(
        "Microsoft Client Secret value:",
        validate=lambda v: bool(v) or "Client Secret value is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if client_secret is None:
        ui.abort()

    tenant_id = questionary.text(
        "Microsoft Azure AD Tenant ID:",
        validate=lambda v: bool(v.strip()) or "Tenant ID is required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if tenant_id is None:
        ui.abort()
    tenant_id = tenant_id.strip()

    organization = questionary.text(
        "Organization short name (optional, leave blank to skip):",
        default="",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if organization is None:
        ui.abort()
    organization = organization.strip()

    ui.success("Microsoft SSO: [highlight]Enabled[/highlight]")
    return {
        "microsoft_sso_enabled": True,
        "microsoft_sso_client_id": client_id,
        "microsoft_sso_client_secret": client_secret,
        "microsoft_sso_tenant_id": tenant_id,
        "microsoft_sso_organization": organization,
    }


def _prompt_credentials(
    step: int,
    total: int,
    state: ProjectState | None = None,
) -> dict:
    """Collect credentials. If state is provided, offers to reuse provisioning creds."""
    ui.step_header(step, total, "Credentials")

    ui.info(
        "A GitHub Personal Access Token + the org/repo names for the two "
        "private packages this setup installs."
    )
    git_access_token = questionary.password(
        "GitHub Personal Access Token:",
        validate=lambda v: len(v.strip()) > 0 or "Required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if git_access_token is None:
        ui.abort()
    git_access_token = git_access_token.strip()
    ui.success("GitHub token provided")

    # Configurable so a fork or non-default deployment can point at its
    # own repos. Defaults match the canonical IBL setup; press Enter to
    # accept. The values are baked into the ansible install URL for
    # iblai-prod-images and shown back in the pre-flight notice.
    github_org = questionary.text(
        "GitHub org owning the private packages:",
        default="iblai",
        validate=lambda v: len(v.strip()) > 0 or "Required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if github_org is None:
        ui.abort()
    github_org = github_org.strip()

    cli_ops_repo = questionary.text(
        "CLI ops repo (or repo/subdir for monorepo):",
        default="iblai-cli-ops",
        instruction="(e.g. iblai-cli-ops, or <client>-iblai-infra-ops/iblai-cli-ops)",
        validate=lambda v: len(v.strip()) > 0 or "Required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if cli_ops_repo is None:
        ui.abort()
    cli_ops_repo = cli_ops_repo.strip()

    prod_images_repo = questionary.text(
        "Prod images repo (or repo/subdir for monorepo):",
        default="iblai-prod-images",
        instruction="(e.g. iblai-prod-images, or <client>-iblai-infra-ops/<client>-iblai-prod-images)",
        validate=lambda v: len(v.strip()) > 0 or "Required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if prod_images_repo is None:
        ui.abort()
    prod_images_repo = prod_images_repo.strip()
    # Echo bare repo names only — the org is collected for the install
    # URL but not surfaced in operator-facing summary text.
    ui.success(
        f"Private packages: [highlight]{cli_ops_repo}[/highlight], "
        f"[highlight]{prod_images_repo}[/highlight]"
    )

    ui.info(
        "AWS credentials for the VM. "
        "Must have access to ECR (iblai-dm-pro and iblai-edx-pro images) and S3 buckets."
    )

    aws_key_id = ""
    aws_secret = ""
    aws_region = ""

    # If we have state with access keys, offer to reuse. GCP-provisioned
    # states carry no AWS credentials (config.credentials is None) — the
    # operator supplies fresh S3/ECR keys below, exactly like bootstrap.
    creds = state.config.credentials if state is not None else None
    if creds is not None:
        aws_region = creds.region
        if creds.access_key_id and creds.secret_access_key:
            reuse = questionary.confirm(
                "Use the same AWS credentials from provisioning?",
                default=True,
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if reuse is None:
                ui.abort()
            if reuse:
                aws_key_id = creds.access_key_id
                aws_secret = creds.secret_access_key
                ui.success("Using provisioning credentials for VM")

    if not aws_key_id:
        aws_key_id = questionary.text(
            "AWS Access Key ID:",
            validate=lambda v: len(v.strip()) > 0 or "Required",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if aws_key_id is None:
            ui.abort()
        aws_key_id = aws_key_id.strip()

        aws_secret = questionary.password(
            "AWS Secret Access Key:",
            validate=lambda v: len(v.strip()) > 0 or "Required",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if aws_secret is None:
            ui.abort()
        aws_secret = aws_secret.strip()

    if not aws_region:
        aws_region = questionary.text(
            "AWS region:",
            default="us-east-1",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if aws_region is None:
            ui.abort()
        aws_region = aws_region.strip()

    openai_api_key = ""
    ui.info("OpenAI API key enables AI mentor features. Leave blank to skip.")
    openai_input = questionary.password(
        "OpenAI API Key (optional):",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if openai_input is None:
        ui.abort()
    openai_api_key = openai_input.strip()
    if openai_api_key:
        ui.success("OpenAI API key provided")
    else:
        ui.muted("Skipped — can be configured later in DM admin")

    ui.info("Super admin account for the platform (LMS and Data Manager).")

    admin_username = questionary.text(
        "Admin username:",
        default="platform_admin",
        validate=_validate_admin_username,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if admin_username is None:
        ui.abort()
    admin_username = admin_username.strip()

    admin_email = questionary.text(
        "Admin email:",
        validate=lambda v: (
            "@" in v.strip() or "Enter a valid email address"
        ),
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if admin_email is None:
        ui.abort()
    admin_email = admin_email.strip()

    admin_password = questionary.password(
        "Admin password:",
        validate=lambda v: len(v.strip()) >= 8 or "Must be at least 8 characters",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if admin_password is None:
        ui.abort()
    admin_password = admin_password.strip()
    ui.success(f"Admin: [highlight]{admin_username}[/highlight] ({admin_email})")

    return {
        "git_access_token": git_access_token,
        "github_org": github_org,
        "cli_ops_repo": cli_ops_repo,
        "prod_images_repo": prod_images_repo,
        "aws_access_key_id": aws_key_id,
        "aws_secret_access_key": aws_secret,
        "aws_default_region": aws_region,
        "openai_api_key": openai_api_key,
        "admin_username": admin_username,
        "admin_email": admin_email,
        "admin_password": admin_password,
    }


# ---------------------------------------------------------------------------
# Setup flow (from Terraform state)
# ---------------------------------------------------------------------------

def prompt_setup(state: ProjectState) -> SetupConfig:
    """Collect variables for Ansible setup from a provisioned environment."""
    target_host = state.outputs.get("instance_public_ip", "")

    # ----- Step 1: SSH Access -----
    ui.step_header(1, SETUP_STEPS, "SSH Access")

    ui.success(f"Target: [highlight]{target_host}[/highlight]")

    ssh_key = _resolve_ssh_key(state)

    if ssh_key:
        ui.success(f"SSH key: [highlight]{ssh_key}[/highlight]")
    else:
        method = state.config.ssh.method
        if method == SSHKeyMethod.EXISTING_FILE:
            ui.info(
                "You provided a public key file during provisioning. "
                "The private key is needed for SSH access."
            )
        elif method == SSHKeyMethod.AWS_KEYPAIR:
            ui.info(
                f"Infrastructure uses AWS key pair [highlight]{state.config.ssh.key_name}[/highlight]. "
                "Provide the matching private key."
            )
        else:
            ui.info("Could not locate the SSH private key from provisioning.")

        ssh_key = _prompt_ssh_key_path()
        ui.success(f"SSH key: [highlight]{ssh_key}[/highlight]")

    validate_key_permissions(ssh_key)

    # ----- Step 2: Platform Configuration -----
    platform = _prompt_platform_config(
        step=2,
        total=SETUP_STEPS,
        base_domain=state.config.dns.base_domain,
    )

    # ----- Step 3: Credentials -----
    cred = _prompt_credentials(step=3, total=SETUP_STEPS, state=state)

    # Resolve the iblai-cli-ops tag from the prod-images pin (needs the
    # GitHub token collected above; prompts as a fallback).
    cli_ops_release_tag = _resolve_cli_ops_release_tag(cred, platform["prod_images_tag"])

    return SetupConfig(
        ssh_private_key_path=ssh_key,
        target_host=target_host,
        cli_ops_release_tag=cli_ops_release_tag,
        **platform,
        **cred,
    )


# ---------------------------------------------------------------------------
# Domain selection (ingress-aware)
# ---------------------------------------------------------------------------


def _select_domain(current_domain: str) -> str:
    """Pick a domain from registered ingress endpoints or enter a custom one."""
    from iblai_infra.terraform.state import load_ingress

    entries = load_ingress()

    if entries:
        choices = [
            questionary.Choice(f"{e.name} — {e.domain}", value=e.domain)
            for e in entries
        ]
        choices.append(questionary.Choice("Custom domain...", value="__custom__"))

        selected = questionary.select(
            "Select ingress endpoint:",
            choices=choices,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if selected is None:
            ui.abort()

        if selected != "__custom__":
            return selected

    base_domain = questionary.text(
        "New base domain:",
        default=current_domain,
        validate=lambda v: len(v.strip()) > 0 or "Required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if base_domain is None:
        ui.abort()
    return base_domain.strip()


# ---------------------------------------------------------------------------
# Re-setup flow (existing environment)
# ---------------------------------------------------------------------------


def prompt_resetup(state: ProjectState) -> SetupConfig:
    """Collect variables for re-setup of an existing environment."""
    target_host = state.outputs.get("instance_public_ip", "")

    # ----- Step 1: SSH Access -----
    ui.step_header(1, RESETUP_STEPS, "SSH Access")

    ui.success(f"Target: [highlight]{target_host}[/highlight]")

    ssh_key = _resolve_ssh_key(state)

    if ssh_key:
        ui.success(f"SSH key: [highlight]{ssh_key}[/highlight]")
    else:
        method = state.config.ssh.method
        if method == SSHKeyMethod.EXISTING_FILE:
            ui.info(
                "You provided a public key file during provisioning. "
                "The private key is needed for SSH access."
            )
        elif method == SSHKeyMethod.AWS_KEYPAIR:
            ui.info(
                f"Infrastructure uses AWS key pair [highlight]{state.config.ssh.key_name}[/highlight]. "
                "Provide the matching private key."
            )
        else:
            ui.info("Could not locate the SSH private key from provisioning.")

        ssh_key = _prompt_ssh_key_path()
        ui.success(f"SSH key: [highlight]{ssh_key}[/highlight]")

    validate_key_permissions(ssh_key)

    # ----- Step 2: Platform Configuration -----
    ui.step_header(2, RESETUP_STEPS, "Platform Configuration")

    current_domain = state.config.dns.base_domain
    ui.info(f"Current domain: [highlight]{current_domain}[/highlight]")

    base_domain = _select_domain(current_domain)
    ui.success(f"Domain: [highlight]{base_domain}[/highlight]")

    prod_images_tag = questionary.text(
        "iblai-prod-images release tag:",
        default="main",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if prod_images_tag is None:
        ui.abort()
    prod_images_tag = prod_images_tag.strip() or "main"
    ui.success(f"iblai-prod-images release: [highlight]{prod_images_tag}[/highlight]")

    # ----- Step 3: Credentials -----
    cred = _prompt_credentials(step=3, total=RESETUP_STEPS, state=state)

    cli_ops_release_tag = _resolve_cli_ops_release_tag(cred, prod_images_tag)

    return SetupConfig(
        ssh_private_key_path=ssh_key,
        target_host=target_host,
        base_domain=base_domain,
        prod_images_tag=prod_images_tag,
        cli_ops_release_tag=cli_ops_release_tag,
        is_resetup=True,
        **cred,
    )


# ---------------------------------------------------------------------------
# Bootstrap flow (no Terraform state)
# ---------------------------------------------------------------------------

def _validate_ip(value: str) -> bool | str:
    """Validate an IP address string."""
    import ipaddress
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return "Enter a valid IP address"


def _validate_project_name(value: str) -> bool | str:
    """Validate a project name."""
    v = value.strip().lower()
    if not v:
        return "Required"
    if not v.replace("-", "").replace("_", "").isalnum():
        return "Must be alphanumeric (hyphens and underscores allowed)"
    if len(v) > 32:
        return "Must be 32 characters or fewer"
    return True


def prompt_bootstrap() -> tuple[SetupConfig, dict]:
    """Collect all variables for setting up an existing server."""

    # ----- Step 1: Project -----
    ui.step_header(1, BOOTSTRAP_STEPS, "Project")

    project_name = questionary.text(
        "Project name:",
        validate=_validate_project_name,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if project_name is None:
        ui.abort()
    project_name = project_name.strip().lower()
    ui.success(f"Project: [highlight]{project_name}[/highlight]")

    # ----- Step 2: Server Access -----
    ui.step_header(2, BOOTSTRAP_STEPS, "Server Access")

    target_host = questionary.text(
        "Server IP address:",
        validate=_validate_ip,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if target_host is None:
        ui.abort()
    target_host = target_host.strip()
    ui.success(f"Target: [highlight]{target_host}[/highlight]")

    ssh_key = _prompt_ssh_key_path()
    ui.success(f"SSH key: [highlight]{ssh_key}[/highlight]")
    validate_key_permissions(ssh_key)

    ssh_user = questionary.text(
        "SSH user:",
        default="ubuntu",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if ssh_user is None:
        ui.abort()
    ssh_user = ssh_user.strip()

    # ----- Step 3: Platform Configuration -----
    platform = _prompt_platform_config(step=3, total=BOOTSTRAP_STEPS)

    # ----- Step 4: Credentials -----
    cred = _prompt_credentials(step=4, total=BOOTSTRAP_STEPS)

    cli_ops_release_tag = _resolve_cli_ops_release_tag(cred, platform["prod_images_tag"])

    config = SetupConfig(
        ssh_private_key_path=ssh_key,
        ssh_user=ssh_user,
        target_host=target_host,
        cli_ops_release_tag=cli_ops_release_tag,
        **platform,
        **cred,
    )

    meta = {"project_name": project_name}
    return config, meta
