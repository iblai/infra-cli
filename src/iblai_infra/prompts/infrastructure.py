"""Steps 2 & 3 — Project info, Compute, Network, SSH."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import questionary
from rich.status import Status

from iblai_infra import ui
from iblai_infra.models import (
    AWSCredentials,
    ComputeConfig,
    Environment,
    INSTANCE_TYPES,
    NetworkConfig,
    SSHConfig,
    SSHKeyMethod,
)
from iblai_infra.providers.aws import (
    detect_current_ip,
    get_session,
    list_key_pairs,
)

TOTAL_STEPS = 5

WORKSPACE_DIR = Path.home() / ".iblai-infra"


# ---------------------------------------------------------------------------
# Step 2 — Project & Compute
# ---------------------------------------------------------------------------

def prompt_project_and_compute() -> tuple[str, Environment, ComputeConfig]:
    """Prompt for project identity and compute settings."""

    ui.step_header(2, TOTAL_STEPS, "Project & Compute")

    # ----- project name -----
    project_name = questionary.text(
        "Project name:",
        validate=lambda v: (
            bool(v.strip()) and v.strip().replace("-", "").replace("_", "").isalnum()
        )
        or "Alphanumeric, hyphens, and underscores only",
        style=ui.PROMPT_STYLE,
    ).ask()
    if project_name is None:
        ui.abort()
    project_name = project_name.strip().lower()

    # ----- environment -----
    env = questionary.select(
        "Environment:",
        choices=[
            questionary.Choice("Production", value=Environment.PROD),
            questionary.Choice("Staging", value=Environment.STAGING),
            questionary.Choice("Development", value=Environment.DEV),
        ],
        style=ui.PROMPT_STYLE,
    ).ask()
    if env is None:
        ui.abort()

    # ----- instance type -----
    instance_labels = {
        f"{itype}  — {desc}": itype for itype, desc in INSTANCE_TYPES.items()
    }
    instance_labels["Custom (enter manually)"] = "_custom"

    instance_selection = questionary.autocomplete(
        "Instance type (type to filter):",
        choices=list(instance_labels.keys()),
        default=f"t3.2xlarge  — {INSTANCE_TYPES['t3.2xlarge']}",
        style=ui.PROMPT_STYLE,
        validate=lambda v: v in instance_labels or "Select a valid instance type from the list",
    ).ask()
    if instance_selection is None:
        ui.abort()
    instance_type = instance_labels[instance_selection]

    if instance_type == "_custom":
        instance_type = questionary.text(
            "Enter instance type (e.g. c5.xlarge):",
            validate=lambda v: bool(v.strip()) or "Required",
            style=ui.PROMPT_STYLE,
        ).ask()
        if instance_type is None:
            ui.abort()

    # ----- volume -----
    volume_size = questionary.text(
        "Root volume size in GB:",
        default="50",
        validate=lambda v: (v.isdigit() and int(v) >= 20) or "Must be a number >= 20",
        style=ui.PROMPT_STYLE,
    ).ask()
    if volume_size is None:
        ui.abort()

    volume_type = questionary.select(
        "Volume type:",
        choices=[
            questionary.Choice("gp3", value="gp3"),
            questionary.Choice("gp2", value="gp2"),
            questionary.Choice("io1 (provisioned IOPS)", value="io1"),
        ],
        default="gp3",
        style=ui.PROMPT_STYLE,
    ).ask()
    if volume_type is None:
        ui.abort()

    compute = ComputeConfig(
        instance_type=instance_type,
        volume_size=int(volume_size),
        volume_type=volume_type,
    )
    return project_name, env, compute


# ---------------------------------------------------------------------------
# Step 3 — Network & SSH
# ---------------------------------------------------------------------------

def prompt_network_and_ssh(
    credentials: AWSCredentials,
    project_name: str,
    environment: Environment,
) -> tuple[NetworkConfig, SSHConfig]:
    """Prompt for network (VPC, VPN IP) and SSH key configuration."""

    ui.step_header(3, TOTAL_STEPS, "Network & Access")

    # ----- VPC CIDR -----
    vpc_cidr = questionary.text(
        "VPC CIDR block:",
        default="10.0.0.0/16",
        validate=_validate_cidr,
        style=ui.PROMPT_STYLE,
    ).ask()
    if vpc_cidr is None:
        ui.abort()

    # ----- VPN IP -----
    detected_ip = detect_current_ip()
    if detected_ip:
        ui.info(f"Detected your current IP: [highlight]{detected_ip}[/highlight]")
        use_detected = questionary.confirm(
            "Use this IP for SSH access?",
            default=True,
            style=ui.PROMPT_STYLE,
        ).ask()
        if use_detected is None:
            ui.abort()
        if use_detected:
            vpn_ip = detected_ip
        else:
            vpn_ip = _ask_vpn_ip()
    else:
        ui.muted("Could not auto-detect your IP address.")
        vpn_ip = _ask_vpn_ip()

    ui.info(f"SSH (port 22) will be restricted to [highlight]{vpn_ip}/32[/highlight]")

    network = NetworkConfig(vpc_cidr=vpc_cidr, vpn_ip=vpn_ip)

    # ----- SSH key -----
    ui.newline()
    ssh_choices = [
        questionary.Choice("Generate a new key pair", value=SSHKeyMethod.GENERATE),
        questionary.Choice("Provide an existing public key file", value=SSHKeyMethod.EXISTING_FILE),
    ]

    # Check for existing AWS key pairs
    session = get_session(credentials)
    aws_keys = list_key_pairs(session)
    if aws_keys:
        ssh_choices.append(
            questionary.Choice(
                f"Use an existing AWS key pair ({len(aws_keys)} found)",
                value=SSHKeyMethod.AWS_KEYPAIR,
            )
        )

    ssh_method: SSHKeyMethod = questionary.select(
        "SSH Key:", choices=ssh_choices, style=ui.PROMPT_STYLE,
    ).ask()
    if ssh_method is None:
        ui.abort()

    key_name = f"{project_name}-{environment.value}"
    public_key = None
    private_key_path = None

    if ssh_method == SSHKeyMethod.GENERATE:
        private_key_path, public_key = _generate_keypair(key_name)
        ui.success(f"Key pair generated: [highlight]{private_key_path}[/highlight]")

    elif ssh_method == SSHKeyMethod.EXISTING_FILE:
        pub_path = questionary.path(
            "Path to public key file:",
            validate=lambda p: Path(p).expanduser().exists() or "File not found",
            style=ui.PROMPT_STYLE,
        ).ask()
        if pub_path is None:
            ui.abort()
        public_key = Path(pub_path).expanduser().read_text().strip()
        key_name = Path(pub_path).stem
        ui.success(f"Using public key: [highlight]{pub_path}[/highlight]")

    elif ssh_method == SSHKeyMethod.AWS_KEYPAIR:
        kp_labels = {
            f"{kp.name} ({kp.key_type})": kp.name for kp in aws_keys
        }
        kp_selection = questionary.autocomplete(
            "Select key pair (type to filter):",
            choices=list(kp_labels.keys()),
            style=ui.PROMPT_STYLE,
            validate=lambda v: v in kp_labels or "Select a valid key pair from the list",
        ).ask()
        if kp_selection is None:
            ui.abort()
        key_name = kp_labels[kp_selection]
        # For AWS keypairs, no public key needed — reference by name in Terraform
        ui.success(f"Using AWS key pair: [highlight]{key_name}[/highlight]")

    ssh = SSHConfig(
        method=ssh_method,
        key_name=key_name,
        public_key=public_key,
        private_key_path=private_key_path,
    )
    return network, ssh


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ask_vpn_ip() -> str:
    ip = questionary.text(
        "Your VPN/static IP for SSH access:",
        validate=_validate_ip,
        style=ui.PROMPT_STYLE,
    ).ask()
    if ip is None:
        ui.abort()
    return ip.strip()


def _validate_ip(value: str) -> bool | str:
    import ipaddress

    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return "Enter a valid IPv4 or IPv6 address (e.g. 203.0.113.42)"


def _validate_cidr(value: str) -> bool | str:
    import ipaddress

    try:
        ipaddress.ip_network(value.strip(), strict=False)
        return True
    except ValueError:
        return "Enter a valid CIDR block (e.g. 10.0.0.0/16)"


def _generate_keypair(name: str) -> tuple[Path, str]:
    """Generate an Ed25519 SSH key pair and return (private_path, public_key_str)."""
    keys_dir = WORKSPACE_DIR / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)

    private_path = keys_dir / f"{name}"
    public_path = keys_dir / f"{name}.pub"

    # Remove existing if any
    private_path.unlink(missing_ok=True)
    public_path.unlink(missing_ok=True)

    subprocess.run(
        [
            "ssh-keygen",
            "-t", "ed25519",
            "-f", str(private_path),
            "-N", "",  # no passphrase
            "-C", f"iblai-infra-{name}",
        ],
        check=True,
        capture_output=True,
    )

    # Restrict permissions
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)

    public_key = public_path.read_text().strip()
    return private_path, public_key
