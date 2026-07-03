"""Build a fully-validated `SetupConfig` from a `.env`-style dict.

Non-interactive counterpart to the `setup` wizard's prompts. Powers
`iblai infra setup-env`. Single-server only — the runner rejects
multi/call deployment types upstream.

Two modes mirror the existing wizard:
- **Provisioned-name mode** — caller passes a loaded `ProjectState`;
  this module derives target_host / ssh_key / base_domain / region
  from it and only reads .env for the rest.
- **Free-standing mode** — caller passes ``state=None``; this module
  reads ``TARGET_HOST`` / ``SSH_PRIVATE_KEY_PATH`` / ``BASE_DOMAIN``
  / ``PROJECT_NAME`` from .env and synthesises a `ProjectState` with
  ``provider="bootstrap"`` (matching `_run_setup_interactive`).
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from iblai_infra import ui
from iblai_infra.env_utils import parse_bool, resolve_pinned_cli_ops_tag
from iblai_infra.models import (
    AuthMethod,
    AWSCredentials,
    CertMethod,
    CertificateConfig,
    ComputeConfig,
    DeploymentType,
    DNSConfig,
    Environment,
    InfraConfig,
    NetworkConfig,
    ProjectState,
    RESERVED_ADMIN_USERNAMES,
    RESERVED_PLATFORM_NAMES,
    SetupConfig,
    SSHConfig,
    SSHKeyMethod,
    is_reserved_admin_username,
    is_reserved_platform_name,
)
from iblai_infra.prompts.setup import validate_key_permissions
from iblai_infra.terraform.state import WORKSPACE_ROOT, load_state, save_state


ALWAYS_REQUIRED: tuple[str, ...] = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "ADMIN_USERNAME",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
)
FREESTANDING_REQUIRED: tuple[str, ...] = (
    "PROJECT_NAME",
    "TARGET_HOST",
    "SSH_PRIVATE_KEY_PATH",
    "BASE_DOMAIN",
)

# `GIT_TOKEN` is the canonical name; `GIT_ACCESS_TOKEN` is accepted as
# an alias because that's the field name on `SetupConfig`.
GIT_TOKEN_KEYS = ("GIT_TOKEN", "GIT_ACCESS_TOKEN")

_PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class EnvSetupError(typer.Exit):
    def __init__(self) -> None:
        super().__init__(code=1)


def _fail(message: str, *, hint: str | None = None) -> EnvSetupError:
    ui.error(message)
    if hint:
        ui.muted(hint)
    return EnvSetupError()


def _get_git_token(env: dict[str, str]) -> str:
    for k in GIT_TOKEN_KEYS:
        v = (env.get(k) or "").strip()
        if v:
            return v
    return ""


def build_bootstrap_state_from_env(env: dict[str, str]) -> ProjectState:
    """Build a synthetic `ProjectState` for free-standing-server setup.

    Mirrors `cli._run_setup_interactive` — provider=bootstrap, status=created,
    workspace path under WORKSPACE_ROOT/<name>-bootstrap.

    Side effect: writes the new `state.json` so subsequent
    `iblai infra list` / `destroy` see it.
    """
    missing = [k for k in FREESTANDING_REQUIRED if not env.get(k)]
    if missing:
        ui.error("Missing required free-standing keys in .env:")
        for k in missing:
            ui.muted(f"  - {k}")
        raise EnvSetupError()

    project_name = env["PROJECT_NAME"].strip().lower()
    if not _PROJECT_NAME_RE.match(project_name) or len(project_name) > 32:
        raise _fail(
            f"PROJECT_NAME={project_name!r} is invalid.",
            hint="Use lowercase a-z, 0-9, hyphen, underscore (≤ 32 chars).",
        )

    # Idempotency: if a bootstrap state already exists for this project,
    # reuse it rather than clobbering setup_status / created_at. Mirrors
    # `_run_setup_interactive` (cli.py ~line 1850).
    existing = load_state(project_name)
    if existing is not None and existing.provider == "bootstrap":
        ui.info(
            f"Resuming existing bootstrap state for [highlight]{project_name}[/highlight] "
            f"(setup_status={existing.setup_status or 'pending'})"
        )
        return existing

    ssh_path = Path(env["SSH_PRIVATE_KEY_PATH"]).expanduser()
    if not ssh_path.exists():
        raise _fail(f"SSH_PRIVATE_KEY_PATH not found: {ssh_path}")
    if not validate_key_permissions(ssh_path):
        raise _fail(f"SSH_PRIVATE_KEY_PATH is not readable: {ssh_path}")

    region = (env.get("AWS_DEFAULT_REGION") or "us-east-1").strip()
    base_domain = env["BASE_DOMAIN"].strip().lower()
    target_host = env["TARGET_HOST"].strip()

    state = ProjectState(
        name=project_name,
        provider="bootstrap",
        status="created",
        config=InfraConfig(
            project_name=project_name,
            environment=Environment.DEV,
            credentials=AWSCredentials(
                method=AuthMethod.ACCESS_KEY,
                access_key_id=(env.get("AWS_ACCESS_KEY_ID") or "").strip() or None,
                secret_access_key=(env.get("AWS_SECRET_ACCESS_KEY") or "").strip() or None,
                region=region,
            ),
            network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="0.0.0.0"),
            compute=ComputeConfig(),
            ssh=SSHConfig(
                method=SSHKeyMethod.EXISTING_FILE,
                key_name="bootstrap",
                private_key_path=ssh_path,
            ),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain=base_domain),
        ),
        outputs={"instance_public_ip": target_host},
        workspace_path=str(WORKSPACE_ROOT / f"{project_name}-bootstrap"),
    )
    save_state(state)
    return state


def build_setup_config_from_env(
    env: dict[str, str],
    *,
    state: ProjectState,
) -> SetupConfig:
    """Validate `env` and return a `SetupConfig` ready for `AnsibleRunner`.

    `state` provides the four "where to deploy" fields (target_host,
    ssh key, base_domain, region) by default; the operator may override
    any of them in `.env` for unusual cases (e.g. a manually-promoted
    bastion). All other fields come from `.env`.

    Single-server only — rejects `MULTI` and `CALL` deployment types so
    operators don't accidentally apply the single-server playbook to a
    multi-server stack.
    """
    # Reject multi/call upfront. Existing call-server users still have
    # the wizard; we don't want a foot-gun here.
    deployment = getattr(state.config, "deployment_type", DeploymentType.SINGLE)
    if deployment != DeploymentType.SINGLE:
        raise _fail(
            f"setup-env only supports single-server. State '{state.name}' is "
            f"{deployment.value}.",
            hint="Use [brand]iblai infra setup[/brand] (the wizard) for multi/call.",
        )

    missing = [k for k in ALWAYS_REQUIRED if not env.get(k)]
    git_token = _get_git_token(env)
    if not git_token:
        missing.append("GIT_TOKEN")
    if missing:
        ui.error("Missing required variables in .env:")
        for k in missing:
            ui.muted(f"  - {k}")
        raise EnvSetupError()

    admin_email = env["ADMIN_EMAIL"].strip()
    if "@" not in admin_email:
        raise _fail(f"ADMIN_EMAIL={admin_email!r} is missing '@'.")
    admin_password = env["ADMIN_PASSWORD"]
    if len(admin_password) < 8:
        raise _fail("ADMIN_PASSWORD must be at least 8 characters.")
    admin_username = env["ADMIN_USERNAME"].strip()
    if is_reserved_admin_username(admin_username):
        reserved = ", ".join(sorted(RESERVED_ADMIN_USERNAMES))
        raise _fail(
            f"ADMIN_USERNAME={admin_username!r} is reserved for system use.",
            hint=f"Reserved usernames: {reserved}. Pick a different one (e.g. 'platform_admin').",
        )

    # PLATFORM_NAME: blank/absent → resolves to 'main' (system default
    # tenant, no tenant launch). Explicitly setting it to 'main' is
    # rejected — operators shouldn't pick the reserved name; they should
    # either leave it unset or pick a real tenant key.
    raw_platform_name = env.get("PLATFORM_NAME")
    if raw_platform_name is not None and raw_platform_name.strip():
        candidate = raw_platform_name.strip().lower()
        if is_reserved_platform_name(candidate):
            reserved = ", ".join(sorted(RESERVED_PLATFORM_NAMES))
            raise _fail(
                f"PLATFORM_NAME={candidate!r} is reserved for the system default tenant.",
                hint=(
                    f"Reserved: {reserved}. Leave PLATFORM_NAME unset (or remove the line) "
                    f"to use the default, or pick a tenant key like 'acme'."
                ),
            )
        platform_name = candidate
    else:
        platform_name = "main"

    # Resolve "where to deploy" fields, allowing env to override state.
    target_host = (env.get("TARGET_HOST") or "").strip()
    if not target_host:
        target_host = ((state.outputs or {}).get("instance_public_ip") or "").strip()
    if not target_host:
        raise _fail(
            "TARGET_HOST not set in .env and project state has no instance_public_ip."
        )

    ssh_path_raw = (env.get("SSH_PRIVATE_KEY_PATH") or "").strip()
    if ssh_path_raw:
        ssh_path = Path(ssh_path_raw).expanduser()
    else:
        ssh_path = state.config.ssh.private_key_path
        if ssh_path is None:
            raise _fail(
                "SSH_PRIVATE_KEY_PATH not set in .env and project state has no key path.",
                hint="Set SSH_PRIVATE_KEY_PATH=/path/to/key in your .env.",
            )
    if not Path(ssh_path).exists():
        raise _fail(f"SSH private key not found: {ssh_path}")
    if not validate_key_permissions(Path(ssh_path)):
        raise _fail(f"SSH private key is not readable: {ssh_path}")

    base_domain = (env.get("BASE_DOMAIN") or "").strip().lower()
    if not base_domain:
        base_domain = state.config.dns.base_domain

    region = (env.get("AWS_DEFAULT_REGION") or "").strip()
    if not region:
        # GCP-provisioned states have no AWS credentials block.
        state_creds = state.config.credentials
        region = (state_creds.region if state_creds else "") or "us-east-1"

    # SMTP block — disabled unless host is set.
    smtp_host = (env.get("SMTP_HOST") or "").strip()
    smtp_enabled = bool(smtp_host)
    smtp_port_raw = (env.get("SMTP_PORT") or "587").strip() or "587"
    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        raise _fail(f"SMTP_PORT={smtp_port_raw!r} is not an integer.")

    # Stripe block — disabled unless secret key is set.
    stripe_secret_key = (env.get("STRIPE_SECRET_KEY") or "").strip()
    stripe_enabled = bool(stripe_secret_key)

    google_sso_client_id = (env.get("GOOGLE_SSO_CLIENT_ID") or "").strip()
    google_sso_enabled = bool(google_sso_client_id)

    microsoft_sso_client_id = (env.get("MICROSOFT_SSO_CLIENT_ID") or "").strip()
    microsoft_sso_enabled = bool(microsoft_sso_client_id)

    github_org = (env.get("GITHUB_ORG") or "iblai").strip()
    prod_images_repo_raw = (env.get("PROD_IMAGES_REPO") or "iblai-prod-images").strip()
    prod_images_tag = (env.get("PROD_IMAGES_TAG") or "main").strip()

    # CLI_OPS_RELEASE_TAG is optional: when unset, resolve it from the
    # prod-images [tool.uv.sources] pin (see env_utils), falling back to
    # "main" so the install still points at a real ref.
    cli_ops_tag = (env.get("CLI_OPS_RELEASE_TAG") or "").strip()
    if not cli_ops_tag:
        from iblai_infra.models import parse_repo_path

        pi_repo, pi_subdir = parse_repo_path(prod_images_repo_raw)
        cli_ops_tag = resolve_pinned_cli_ops_tag(
            git_token, github_org, pi_repo, prod_images_tag, subdir=pi_subdir
        )
        if cli_ops_tag:
            ui.info(
                f"iblai-cli-ops [highlight]{cli_ops_tag}[/highlight] "
                f"(pinned by {pi_repo}@{prod_images_tag})"
            )
        else:
            cli_ops_tag = "main"
            ui.warning(
                f"Could not read the iblai-cli-ops pin from {pi_repo}@{prod_images_tag}; "
                "falling back to 'main'. Set CLI_OPS_RELEASE_TAG to override."
            )

    return SetupConfig(
        ssh_private_key_path=Path(ssh_path),
        ssh_user=(env.get("SSH_USER") or "ubuntu").strip(),
        target_host=target_host,
        base_domain=base_domain,
        edx_version=(env.get("EDX_VERSION") or "sumac").strip(),
        env_config=(env.get("ENV_CONFIG") or "single-server").strip(),
        cli_ops_release_tag=cli_ops_tag,
        prod_images_tag=prod_images_tag,
        enable_ai=parse_bool(env.get("ENABLE_AI"), default=True),
        create_playwright_platforms=parse_bool(
            env.get("CREATE_PLAYWRIGHT_PLATFORMS"), default=False
        ),
        aws_access_key_id=env["AWS_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"].strip(),
        aws_default_region=region,
        git_access_token=git_token,
        github_org=github_org,
        cli_ops_repo=(env.get("CLI_OPS_REPO") or "iblai-cli-ops").strip(),
        prod_images_repo=prod_images_repo_raw,
        openai_api_key=(env.get("OPENAI_API_KEY") or "").strip(),
        admin_username=admin_username,
        admin_email=admin_email,
        admin_password=admin_password,
        # SMTP
        smtp_enabled=smtp_enabled,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=(env.get("SMTP_USERNAME") or "").strip(),
        smtp_password=(env.get("SMTP_PASSWORD") or ""),
        smtp_sender_email=(env.get("SMTP_SENDER_EMAIL") or "").strip(),
        smtp_use_tls=parse_bool(env.get("SMTP_USE_TLS"), default=True),
        smtp_use_ssl=parse_bool(env.get("SMTP_USE_SSL"), default=False),
        # Stripe
        stripe_enabled=stripe_enabled,
        stripe_mode=(env.get("STRIPE_MODE") or "test").strip(),
        stripe_secret_key=stripe_secret_key,
        stripe_pub_key=(env.get("STRIPE_PUB_KEY") or "").strip(),
        stripe_pricing_table_id=(env.get("STRIPE_PRICING_TABLE_ID") or "").strip(),
        stripe_pricing_table_id_returning=(
            env.get("STRIPE_PRICING_TABLE_ID_RETURNING") or ""
        ).strip(),
        stripe_webhook_secret=(env.get("STRIPE_WEBHOOK_SECRET") or "").strip(),
        stripe_connect_webhook_secret=(
            env.get("STRIPE_CONNECT_WEBHOOK_SECRET") or ""
        ).strip(),
        # Platform name + SSO
        platform_name=platform_name,
        google_sso_enabled=google_sso_enabled,
        google_sso_client_id=google_sso_client_id,
        google_sso_client_secret=(env.get("GOOGLE_SSO_CLIENT_SECRET") or "").strip(),
        google_sso_organization=(env.get("GOOGLE_SSO_ORGANIZATION") or "").strip(),
        microsoft_sso_enabled=microsoft_sso_enabled,
        microsoft_sso_client_id=microsoft_sso_client_id,
        microsoft_sso_client_secret=(
            env.get("MICROSOFT_SSO_CLIENT_SECRET") or ""
        ).strip(),
        microsoft_sso_tenant_id=(env.get("MICROSOFT_SSO_TENANT_ID") or "").strip(),
        microsoft_sso_organization=(
            env.get("MICROSOFT_SSO_ORGANIZATION") or ""
        ).strip(),
    )
