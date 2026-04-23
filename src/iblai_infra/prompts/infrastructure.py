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
    CALL_INSTANCE_TYPES,
    CallServerConfig,
    ComputeConfig,
    DeploymentType,
    Environment,
    INSTANCE_TYPES,
    MultiServerConfig,
    NetworkConfig,
    SSHConfig,
    SSHKeyMethod,
    generate_password,
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

def prompt_project_and_compute() -> (
    tuple[
        str,
        Environment,
        DeploymentType,
        ComputeConfig,
        MultiServerConfig | None,
        CallServerConfig | None,
    ]
):
    """Prompt for project identity, deployment type, and compute settings.

    Returns a 6-tuple; the last two fields are mutually exclusive:
    - multi_server is set only when deployment_type == MULTI
    - call_server is set only when deployment_type == CALL
    """

    ui.step_header(2, TOTAL_STEPS, "Project & Compute")

    # ----- project name -----
    project_name = questionary.text(
        "Project name:",
        validate=lambda v: (
            bool(v.strip()) and v.strip().replace("-", "").replace("_", "").isalnum()
        )
        or "Alphanumeric, hyphens, and underscores only",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
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
        qmark=ui.QMARK,
    ).ask()
    if env is None:
        ui.abort()

    # ----- deployment type -----
    deployment_type = questionary.select(
        "Deployment type:",
        choices=[
            questionary.Choice(
                "Single server  — all services on one instance",
                value=DeploymentType.SINGLE,
            ),
            questionary.Choice(
                "Multi-server   — app servers + services server + optional managed DBs",
                value=DeploymentType.MULTI,
            ),
            questionary.Choice(
                "Call server    — standalone LiveKit in an isolated VPC",
                value=DeploymentType.CALL,
            ),
        ],
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if deployment_type is None:
        ui.abort()

    multi_server = None
    call_server = None

    if deployment_type == DeploymentType.MULTI:
        multi_server = _prompt_multi_server_config()
        # For multi-server, compute is a placeholder (not used by the template)
        compute = ComputeConfig()
        return project_name, env, deployment_type, compute, multi_server, call_server

    if deployment_type == DeploymentType.CALL:
        call_server = _prompt_call_server_config()
        # Sync compute so the shared tfvars emitter produces the right values
        compute = ComputeConfig(
            instance_type=call_server.instance_type,
            volume_size=call_server.volume_size,
            volume_type=call_server.volume_type,
        )
        return project_name, env, deployment_type, compute, multi_server, call_server

    # ----- single-server: instance type -----
    instance_labels = {
        f"{itype}  — {desc}": itype for itype, desc in INSTANCE_TYPES.items()
    }
    instance_labels["Custom (enter manually)"] = "_custom"

    instance_selection = questionary.autocomplete(
        "Instance type (type to filter):",
        choices=list(instance_labels.keys()),
        default=f"t3.2xlarge  — {INSTANCE_TYPES['t3.2xlarge']}",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
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
            qmark=ui.QMARK,
        ).ask()
        if instance_type is None:
            ui.abort()

    # ----- single-server: volume -----
    volume_size = questionary.text(
        "Root volume size in GB:",
        default="50",
        validate=lambda v: (v.isdigit() and int(v) >= 20) or "Must be a number >= 20",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
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
        qmark=ui.QMARK,
    ).ask()
    if volume_type is None:
        ui.abort()

    compute = ComputeConfig(
        instance_type=instance_type,
        volume_size=int(volume_size),
        volume_type=volume_type,
    )
    return project_name, env, deployment_type, compute, multi_server, call_server


def _prompt_multi_server_config() -> MultiServerConfig:
    """Prompt for multi-server configuration (app servers, services, managed DBs)."""

    ui.newline()
    ui.info("[bold]App Servers[/bold] (behind the ALB)")

    app_count = questionary.text(
        "Number of app servers:",
        default="2",
        validate=lambda v: (v.isdigit() and 2 <= int(v) <= 10) or "Must be 2-10",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if app_count is None:
        ui.abort()

    app_instance_labels = {
        f"{itype}  — {desc}": itype for itype, desc in INSTANCE_TYPES.items()
    }
    app_instance_labels["Custom (enter manually)"] = "_custom"

    app_selection = questionary.autocomplete(
        "App server instance type:",
        choices=list(app_instance_labels.keys()),
        default=f"t3.2xlarge  — {INSTANCE_TYPES['t3.2xlarge']}",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
        validate=lambda v: v in app_instance_labels or "Select from the list",
    ).ask()
    if app_selection is None:
        ui.abort()
    app_instance_type = app_instance_labels[app_selection]
    if app_instance_type == "_custom":
        app_instance_type = questionary.text(
            "Enter instance type:",
            validate=lambda v: bool(v.strip()) or "Required",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if app_instance_type is None:
            ui.abort()

    app_volume = questionary.text(
        "App server volume size (GB):",
        default="250",
        validate=lambda v: (v.isdigit() and int(v) >= 20) or "Must be >= 20",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if app_volume is None:
        ui.abort()

    ui.newline()
    ui.info("[bold]Services Server[/bold] (private subnet)")

    svc_instance_labels = {
        f"{itype}  — {desc}": itype for itype, desc in INSTANCE_TYPES.items()
    }
    svc_instance_labels["Custom (enter manually)"] = "_custom"

    svc_selection = questionary.autocomplete(
        "Services server instance type:",
        choices=list(svc_instance_labels.keys()),
        default=f"t3.2xlarge  — {INSTANCE_TYPES['t3.2xlarge']}",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
        validate=lambda v: v in svc_instance_labels or "Select from the list",
    ).ask()
    if svc_selection is None:
        ui.abort()
    svc_instance_type = svc_instance_labels[svc_selection]
    if svc_instance_type == "_custom":
        svc_instance_type = questionary.text(
            "Enter instance type:",
            validate=lambda v: bool(v.strip()) or "Required",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if svc_instance_type is None:
            ui.abort()

    svc_volume = questionary.text(
        "Services server volume size (GB):",
        default="500",
        validate=lambda v: (v.isdigit() and int(v) >= 20) or "Must be >= 20",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if svc_volume is None:
        ui.abort()

    # ----- managed databases -----
    ui.newline()
    ui.info("[bold]Managed Services[/bold] (optional)")

    enable_mysql = questionary.confirm(
        "Enable managed MySQL (RDS)?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enable_mysql is None:
        ui.abort()

    enable_postgres = questionary.confirm(
        "Enable managed PostgreSQL (RDS)?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enable_postgres is None:
        ui.abort()

    enable_redis = questionary.confirm(
        "Enable managed Redis (ElastiCache)?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enable_redis is None:
        ui.abort()

    # Generate secrets for managed services at runtime
    mysql_password = generate_password() if enable_mysql else None
    postgres_password = generate_password() if enable_postgres else None
    redis_auth_token = generate_password(32) if enable_redis else None

    return MultiServerConfig(
        app_server_count=int(app_count),
        app_server_instance_type=app_instance_type,
        app_server_volume_size=int(app_volume),
        services_instance_type=svc_instance_type,
        services_volume_size=int(svc_volume),
        enable_mysql=enable_mysql,
        enable_postgres=enable_postgres,
        enable_redis=enable_redis,
        mysql_password=mysql_password,
        postgres_password=postgres_password,
        redis_auth_token=redis_auth_token,
    )


def _prompt_call_server_config() -> CallServerConfig:
    """Prompt for call-server (LiveKit) configuration."""
    ui.newline()
    ui.info("[bold]Call Server[/bold] (LiveKit) — isolated VPC, direct public EIP")

    # Call-server uses a dedicated instance-type list sized for LiveKit
    # (starts at t3.medium — the single-server INSTANCE_TYPES starts at
    # t3.xlarge which is overkill for a small LiveKit node).
    instance_labels = {
        f"{itype}  — {desc}": itype for itype, desc in CALL_INSTANCE_TYPES.items()
    }
    instance_labels["Custom (enter manually)"] = "_custom"

    # Default must match the exact key-format built above so the validator accepts it.
    _default_label = next(k for k, v in instance_labels.items() if v == "t3.large")

    selection = questionary.autocomplete(
        "Call server instance type (LiveKit is CPU-bound — upsize for heavy workloads):",
        choices=list(instance_labels.keys()),
        default=_default_label,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
        validate=lambda v: v in instance_labels or "Select from the list",
    ).ask()
    if selection is None:
        ui.abort()
    instance_type = instance_labels[selection]
    if instance_type == "_custom":
        instance_type = questionary.text(
            "Enter instance type:",
            validate=lambda v: bool(v.strip()) or "Required",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if instance_type is None:
            ui.abort()

    volume_size = questionary.text(
        "Root volume size (GB):",
        default="40",
        validate=lambda v: (v.isdigit() and int(v) >= 20) or "Must be >= 20",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if volume_size is None:
        ui.abort()

    enable_sip = questionary.confirm(
        "Enable SIP stack? (opens 5060 TCP+UDP, 5061 TLS, 10000-20000 UDP for RTP)",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enable_sip is None:
        ui.abort()

    return CallServerConfig(
        instance_type=instance_type,
        volume_size=int(volume_size),
        enable_sip=enable_sip,
    )


# ---------------------------------------------------------------------------
# Step 3 — Network & SSH
# ---------------------------------------------------------------------------

def prompt_network_and_ssh(
    credentials: AWSCredentials,
    project_name: str,
    environment: Environment,
    default_vpc_cidr: str = "10.0.0.0/16",
) -> tuple[NetworkConfig, SSHConfig]:
    """Prompt for network (VPC, VPN IP) and SSH key configuration."""

    ui.step_header(3, TOTAL_STEPS, "Network & Access")

    # ----- VPC CIDR -----
    vpc_cidr = questionary.text(
        "VPC CIDR block:",
        default=default_vpc_cidr,
        validate=_validate_cidr,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
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
            qmark=ui.QMARK,
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
        "SSH Key:",
        choices=ssh_choices,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
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
            qmark=ui.QMARK,
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
            qmark=ui.QMARK,
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
        qmark=ui.QMARK,
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
