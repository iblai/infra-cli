"""Setup prompts — collect variables for Ansible VM bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

import questionary

from iblai_infra import ui
from iblai_infra.models import ProjectState, SetupConfig, SSHKeyMethod

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


def _validate_key_permissions(path: Path) -> bool:
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

    edx_version = "sumac"
    ui.success(f"Open edX version: [highlight]Sumac[/highlight]")

    env_config = "single-server"
    ui.success(f"Server type: [highlight]Single Server[/highlight]")

    cli_ops_release_tag = questionary.text(
        "iblai-cli-ops release tag:",
        default="3.19.0",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if cli_ops_release_tag is None:
        ui.abort()
    cli_ops_release_tag = cli_ops_release_tag.strip()
    ui.success(f"iblai-cli-ops release: [highlight]{cli_ops_release_tag}[/highlight]")

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

    return {
        "base_domain": base_domain,
        "edx_version": edx_version,
        "env_config": env_config,
        "cli_ops_release_tag": cli_ops_release_tag,
        "enable_ai": enable_ai,
    }


def _prompt_credentials(
    step: int,
    total: int,
    state: ProjectState | None = None,
) -> dict:
    """Collect credentials. If state is provided, offers to reuse provisioning creds."""
    ui.step_header(step, total, "Credentials")

    ui.info("A GitHub Personal Access Token is needed to install iblai-cli-ops on the VM.")
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

    ui.info(
        "AWS credentials for the VM. "
        "Must have access to ECR (iblai-dm-pro and iblai-edx-pro images) and S3 buckets."
    )

    aws_key_id = ""
    aws_secret = ""
    aws_region = ""

    # If we have state with access keys, offer to reuse
    if state is not None:
        creds = state.config.credentials
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
        default="ibl_admin",
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

    _validate_key_permissions(ssh_key)

    # ----- Step 2: Platform Configuration -----
    platform = _prompt_platform_config(
        step=2,
        total=SETUP_STEPS,
        base_domain=state.config.dns.base_domain,
    )

    # ----- Step 3: Credentials -----
    cred = _prompt_credentials(step=3, total=SETUP_STEPS, state=state)

    return SetupConfig(
        ssh_private_key_path=ssh_key,
        target_host=target_host,
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

    _validate_key_permissions(ssh_key)

    # ----- Step 2: Platform Configuration -----
    ui.step_header(2, RESETUP_STEPS, "Platform Configuration")

    current_domain = state.config.dns.base_domain
    ui.info(f"Current domain: [highlight]{current_domain}[/highlight]")

    base_domain = _select_domain(current_domain)
    ui.success(f"Domain: [highlight]{base_domain}[/highlight]")

    cli_ops_release_tag = questionary.text(
        "iblai-cli-ops release tag:",
        default="3.19.0",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if cli_ops_release_tag is None:
        ui.abort()
    cli_ops_release_tag = cli_ops_release_tag.strip()
    ui.success(f"iblai-cli-ops release: [highlight]{cli_ops_release_tag}[/highlight]")

    # ----- Step 3: Credentials -----
    cred = _prompt_credentials(step=3, total=RESETUP_STEPS, state=state)

    return SetupConfig(
        ssh_private_key_path=ssh_key,
        target_host=target_host,
        base_domain=base_domain,
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
    _validate_key_permissions(ssh_key)

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

    config = SetupConfig(
        ssh_private_key_path=ssh_key,
        ssh_user=ssh_user,
        target_host=target_host,
        **platform,
        **cred,
    )

    meta = {"project_name": project_name}
    return config, meta
