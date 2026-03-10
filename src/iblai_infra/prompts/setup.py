"""Setup prompts — collect variables for Ansible VM bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

import questionary

from iblai_infra import ui
from iblai_infra.models import ProjectState, SetupConfig, SSHKeyMethod

TOTAL_STEPS = 3


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
# Main prompt flow
# ---------------------------------------------------------------------------

def prompt_setup(state: ProjectState) -> SetupConfig:
    """Collect all variables needed for Ansible setup."""
    target_host = state.outputs.get("instance_public_ip", "")

    # ----- Step 1: SSH Access -----
    ui.step_header(1, TOTAL_STEPS, "SSH Access")

    ui.success(f"Target: [highlight]{target_host}[/highlight]")

    # Resolve SSH key
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
    ui.step_header(2, TOTAL_STEPS, "Platform Configuration")

    # Domain (auto-populated, let user confirm)
    base_domain = state.config.dns.base_domain
    ui.success(f"Domain: [highlight]{base_domain}[/highlight]")

    # Open edX version
    edx_version = "sumac"
    ui.success(f"Open edX version: [highlight]Sumac[/highlight]")

    # Environment config
    env_config = "single-server"
    ui.success(f"Server type: [highlight]Single Server[/highlight]")

    # Release image tags
    dm_image_tag = questionary.text(
        "DM release tag:",
        default="4.190.0-ai",
        style=ui.PROMPT_STYLE,
    ).ask()
    if dm_image_tag is None:
        ui.abort()
    dm_image_tag = dm_image_tag.strip()
    ui.success(f"DM image tag: [highlight]{dm_image_tag}[/highlight]")

    edx_image_tag = questionary.text(
        "edX release tag:",
        default="sumac.2.4.13",
        style=ui.PROMPT_STYLE,
    ).ask()
    if edx_image_tag is None:
        ui.abort()
    edx_image_tag = edx_image_tag.strip()
    ui.success(f"edX image tag: [highlight]{edx_image_tag}[/highlight]")

    # ----- Step 3: Credentials -----
    ui.step_header(3, TOTAL_STEPS, "Credentials")

    # GitHub PAT for cloning ibl-cli-ops
    ui.info("A GitHub Personal Access Token is needed to install ibl-cli-ops on the VM.")
    git_access_token = questionary.password(
        "GitHub Personal Access Token:",
        validate=lambda v: len(v.strip()) > 0 or "Required",
        style=ui.PROMPT_STYLE,
    ).ask()
    if git_access_token is None:
        ui.abort()
    git_access_token = git_access_token.strip()
    ui.success("GitHub token provided")

    # AWS credentials for the VM
    ui.info(
        "AWS credentials will be configured on the VM. "
        "They must have access to ECR (iblai-dm-pro and iblai-edx-pro images) and S3 buckets."
    )

    creds = state.config.credentials
    aws_key_id = ""
    aws_secret = ""
    aws_region = creds.region

    # If provisioning used access keys, offer to reuse
    if creds.access_key_id and creds.secret_access_key:
        reuse = questionary.confirm(
            "Use the same AWS credentials from provisioning?",
            default=True,
            style=ui.PROMPT_STYLE,
        ).ask()
        if reuse is None:
            ui.abort()
        if reuse:
            aws_key_id = creds.access_key_id
            aws_secret = creds.secret_access_key
            ui.success("Using provisioning credentials for VM")

    if not aws_key_id:
        aws_key_id = questionary.text(
            "AWS Access Key ID (for the VM):",
            validate=lambda v: len(v.strip()) > 0 or "Required",
            style=ui.PROMPT_STYLE,
        ).ask()
        if aws_key_id is None:
            ui.abort()
        aws_key_id = aws_key_id.strip()

        aws_secret = questionary.password(
            "AWS Secret Access Key (for the VM):",
            validate=lambda v: len(v.strip()) > 0 or "Required",
            style=ui.PROMPT_STYLE,
        ).ask()
        if aws_secret is None:
            ui.abort()
        aws_secret = aws_secret.strip()

    return SetupConfig(
        ssh_private_key_path=ssh_key,
        target_host=target_host,
        base_domain=base_domain,
        edx_version=edx_version,
        env_config=env_config,
        dm_image_tag=dm_image_tag,
        edx_image_tag=edx_image_tag,
        aws_access_key_id=aws_key_id,
        aws_secret_access_key=aws_secret,
        aws_default_region=aws_region,
        git_access_token=git_access_token,
    )
