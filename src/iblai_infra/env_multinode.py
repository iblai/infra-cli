"""Build multi-node project state and run variables from a `.env`-style dict.

Powers the per-role commands (`iblai infra app|stores|sandbox|call ...`).
They all read the same keys, so one file can serve every command;
`.env.multinode.example` is the schema's source of truth.

What is persisted (in the project's ``state.json``): hosts, NODE_IDs and the
store layout - a `MultiNodeTopology`. What is not: every credential, which
is read from the .env on each run and passed to Ansible as extra-vars.
"""

from __future__ import annotations

import re
from pathlib import Path

from iblai_infra import ui
from iblai_infra.env_setup import EnvSetupError, _fail, _get_git_token, build_setup_config_from_env
from iblai_infra.env_utils import parse_bool, resolve_pinned_cli_ops_tag
from iblai_infra.models import (
    AuthMethod,
    AWSCredentials,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DeploymentType,
    DNSConfig,
    Environment,
    InfraConfig,
    ManagedEndpoint,
    MultiNodeTopology,
    NetworkConfig,
    ProjectState,
    SetupConfig,
    SSHConfig,
    SSHKeyMethod,
    StoreLocation,
    parse_repo_path,
)
from iblai_infra.prompts.setup import validate_key_permissions
from iblai_infra.terraform import state as tf_state
from iblai_infra.terraform.state import load_state, save_state

_PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# store -> (.env prefix, default port, default admin user, has a TLS switch)
STORES: dict[str, tuple[str, int, str]] = {
    "mysql": ("MYSQL", 3306, "admin"),
    "postgres": ("POSTGRES", 5432, "postgres"),
    "redis_edx": ("REDIS_EDX", 6379, ""),
    "redis_dm": ("REDIS_DM", 6379, ""),
}


def _hosts(value: str | None) -> list[str]:
    return [h.strip() for h in (value or "").split(",") if h.strip()]


def _project_name(env: dict[str, str]) -> str:
    name = (env.get("PROJECT_NAME") or "").strip().lower()
    if not name:
        raise _fail("PROJECT_NAME is not set in the .env.")
    if not _PROJECT_NAME_RE.match(name) or len(name) > 32:
        raise _fail(
            f"PROJECT_NAME={name!r} is invalid.",
            hint="Use lowercase a-z, 0-9, hyphen, underscore (<= 32 chars).",
        )
    return name


def _store_layout(env: dict[str, str]) -> dict:
    """The store choices and managed endpoints a .env describes."""
    layout: dict = {}
    for store, (prefix, port, user) in STORES.items():
        raw = (env.get(prefix) or "stores").strip().lower()
        try:
            location = StoreLocation(raw)
        except ValueError:
            raise _fail(f"{prefix}={raw!r} must be 'stores' or 'managed'.")
        layout[store] = location
        if location == StoreLocation.MANAGED:
            host = (env.get(f"{prefix}_HOST") or "").strip()
            if not host:
                raise _fail(f"{prefix}=managed needs {prefix}_HOST.")
            port_raw = (env.get(f"{prefix}_PORT") or str(port)).strip()
            if not port_raw.isdigit():
                raise _fail(f"{prefix}_PORT={port_raw!r} is not a port number.")
            username = (
                env.get(f"{prefix}_ROOT_USERNAME") or env.get(f"{prefix}_ADMIN_USERNAME") or user
            ).strip()
            layout[f"{store}_endpoint"] = ManagedEndpoint(
                host=host,
                port=int(port_raw),
                tls=parse_bool(env.get(f"{prefix}_TLS"), default=True),
                username=username,
            )
        else:
            layout[f"{store}_endpoint"] = None
    return layout


def _ssh_key(env: dict[str, str], fallback: Path | None = None) -> Path:
    raw = (env.get("SSH_PRIVATE_KEY_PATH") or "").strip()
    path = Path(raw).expanduser() if raw else fallback
    if path is None:
        raise _fail("SSH_PRIVATE_KEY_PATH is not set in the .env.")
    if not path.exists():
        raise _fail(f"SSH private key not found: {path}")
    if not validate_key_permissions(path):
        raise _fail(f"SSH private key is not readable: {path}")
    return path


def load_or_create_state(env: dict[str, str], *, create: bool = False) -> ProjectState:
    """The project's state, its topology refreshed from the .env.

    ``create`` (``app init``) makes the project on first use; every other
    command requires it to exist. Host keys in the .env add hosts to the
    topology but never move an existing role: the hub and the stores node
    hold the deployment's data and config, so changing them is not a .env edit.
    """
    name = _project_name(env)
    state = load_state(name)

    if state is not None and getattr(state.config, "deployment_type", None) != DeploymentType.MULTI_NODE:
        raise _fail(
            f"Project '{name}' exists and is not a multi-node deployment.",
            hint="Pick another PROJECT_NAME for the multi-node deployment.",
        )
    if state is None and not create:
        raise _fail(
            f"No multi-node project '{name}'.",
            hint="Start with [brand]iblai infra app init -f <.env>[/brand].",
        )

    hub = (env.get("HUB_HOST") or "").strip()
    stores = (env.get("STORES_HOST") or "").strip()
    apps = _hosts(env.get("APP_HOSTS"))
    sandbox = (env.get("SANDBOX_HOST") or "").strip()

    if state is None:
        missing = [k for k, v in (("HUB_HOST", hub), ("STORES_HOST", stores), ("BASE_DOMAIN", env.get("BASE_DOMAIN"))) if not v]
        if missing:
            ui.error("Missing required variables in .env:")
            for k in missing:
                ui.muted(f"  - {k}")
            raise EnvSetupError()
        key = _ssh_key(env)
        base_domain = env["BASE_DOMAIN"].strip().lower()
        try:
            topo = MultiNodeTopology(
                hub=hub,
                apps=[a for a in apps if a != hub],
                stores=stores,
                sandbox=sandbox,
                ssh_user=(env.get("SSH_USER") or "ubuntu").strip(),
                ssh_jump_host=(env.get("SSH_JUMP_HOST") or "").strip(),
                stores_private_ip=(env.get("STORES_PRIVATE_IP") or "").strip(),
                node_id_prefix=(env.get("NODE_ID_PREFIX") or name).strip(),
                sandbox_domain=(env.get("SANDBOX_DOMAIN") or f"sandbox.{base_domain}").strip().lower(),
                **_store_layout(env),
            )
        except ValueError as exc:
            raise _fail(f"Invalid topology: {exc}")
        topo.assign_node_ids()
        state = ProjectState(
            name=name,
            provider="multi-node",
            status="created",
            config=InfraConfig(
                project_name=name,
                environment=Environment.PROD,
                deployment_type=DeploymentType.MULTI_NODE,
                credentials=AWSCredentials(
                    method=AuthMethod.ACCESS_KEY,
                    region=(env.get("AWS_DEFAULT_REGION") or "us-east-1").strip(),
                ),
                network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="0.0.0.0"),
                compute=ComputeConfig(),
                ssh=SSHConfig(method=SSHKeyMethod.EXISTING_FILE, key_name="multi-node", private_key_path=key),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain=base_domain),
            ),
            # Read at call time so a patched WORKSPACE_ROOT (tests) applies.
            workspace_path=str(tf_state.WORKSPACE_ROOT / f"{name}-multinode"),
        )
        save_topology(state, topo)
        return state

    topo = topology_of(state)
    if hub and hub != topo.hub:
        raise _fail(f"HUB_HOST={hub} but project '{name}' was set up with hub {topo.hub}.")
    if stores and stores != topo.stores:
        raise _fail(f"STORES_HOST={stores} but project '{name}' uses stores node {topo.stores}.")
    changed = False
    for host in apps:
        if host != topo.hub and host not in topo.apps:
            topo.apps.append(host)
            changed = True
    if sandbox and sandbox != topo.sandbox:
        topo.sandbox = sandbox
        changed = True
    if (env.get("SANDBOX_DOMAIN") or "").strip() and env["SANDBOX_DOMAIN"].strip().lower() != topo.sandbox_domain:
        topo.sandbox_domain = env["SANDBOX_DOMAIN"].strip().lower()
        changed = True
    if changed:
        try:
            topo = MultiNodeTopology.model_validate(topo.model_dump())
        except ValueError as exc:
            raise _fail(f"Invalid topology: {exc}")
        topo.assign_node_ids()
        save_topology(state, topo)
    return state


def topology_of(state: ProjectState) -> MultiNodeTopology:
    data = (state.outputs or {}).get("multi_node")
    if not data:
        raise _fail(f"Project '{state.name}' has no multi-node topology recorded.")
    return MultiNodeTopology.model_validate(data)


def save_topology(state: ProjectState, topo: MultiNodeTopology) -> None:
    """Persist the topology. `instance_public_ip` is the hub, so the
    post-setup feature commands (which target one host) reach it."""
    state.outputs = {
        **(state.outputs or {}),
        "instance_public_ip": topo.hub,
        "multi_node": topo.model_dump(mode="json"),
    }
    save_state(state)


def topology_vars(topo: MultiNodeTopology) -> dict:
    """The non-secret extra-vars every multi-node playbook reads."""
    out: dict = {
        "stores_private_ip": topo.stores_private_ip,
        "sandbox_domain": topo.sandbox_domain,
    }
    for store in STORES:
        out[f"{store}_location"] = getattr(topo, store).value
        endpoint: ManagedEndpoint | None = getattr(topo, f"{store}_endpoint")
        if endpoint is not None:
            out[f"{store}_host"] = endpoint.host
            out[f"{store}_port"] = endpoint.port
            out[f"{store}_tls"] = endpoint.tls
    if topo.mysql_endpoint is not None:
        out["mysql_root_username"] = topo.mysql_endpoint.username
    if topo.postgres_endpoint is not None:
        out["postgres_admin_username"] = topo.postgres_endpoint.username
    return out


def store_secret_vars(env: dict[str, str], topo: MultiNodeTopology) -> dict:
    """Managed stores' credentials, required for each managed store that has one."""
    out: dict = {}
    missing: list[str] = []
    if topo.mysql == StoreLocation.MANAGED:
        out["mysql_root_password"] = env.get("MYSQL_ROOT_PASSWORD", "")
        if not out["mysql_root_password"]:
            missing.append("MYSQL_ROOT_PASSWORD")
    if topo.postgres == StoreLocation.MANAGED:
        out["postgres_admin_password"] = env.get("POSTGRES_ADMIN_PASSWORD", "")
        if not out["postgres_admin_password"]:
            missing.append("POSTGRES_ADMIN_PASSWORD")
    if topo.redis_edx == StoreLocation.MANAGED:
        out["redis_edx_password"] = env.get("REDIS_EDX_PASSWORD", "")
    if topo.redis_dm == StoreLocation.MANAGED:
        # The DM builds its Redis URL with a password and Langfuse requires one.
        out["redis_dm_password"] = env.get("REDIS_DM_PASSWORD", "")
        if not out["redis_dm_password"]:
            missing.append("REDIS_DM_PASSWORD")
    if missing:
        ui.error("Missing managed-store credentials in .env:")
        for k in missing:
            ui.muted(f"  - {k}")
        raise EnvSetupError()
    return out


def platform_vars(env: dict[str, str]) -> dict:
    """Non-secret platform choices the multi-node playbooks read."""
    return {
        "timescaledb": (env.get("TIMESCALEDB") or "auto").strip().lower(),
        "mongodb_auth": parse_bool(env.get("MONGODB_AUTH"), default=True),
    }


def wheel_vars(env: dict[str, str], *, prefix: str = "") -> dict:
    """Local wheels to install instead of the GitHub repos (both or neither)."""
    cli = (env.get(f"{prefix}CLI_OPS_WHEEL") or "").strip()
    images = (env.get(f"{prefix}PROD_IMAGES_WHEEL") or "").strip()
    if bool(cli) != bool(images):
        raise _fail(f"Set both {prefix}CLI_OPS_WHEEL and {prefix}PROD_IMAGES_WHEEL, or neither.")
    for key, value in ((f"{prefix}CLI_OPS_WHEEL", cli), (f"{prefix}PROD_IMAGES_WHEEL", images)):
        if value and not Path(value).expanduser().is_file():
            raise _fail(f"{key} not found: {value}")
    return {
        "cli_ops_wheel": str(Path(cli).expanduser()) if cli else "",
        "prod_images_wheel": str(Path(images).expanduser()) if images else "",
    }


def platform_config(env: dict[str, str], state: ProjectState) -> SetupConfig:
    """The full platform config for the hub (`app init` / `app setup`)."""
    env = dict(env)
    wheels = bool(wheel_vars(env)["cli_ops_wheel"])
    if wheels and not (env.get("CLI_OPS_RELEASE_TAG") or "").strip():
        # Nothing installs from GitHub, so there is no pinned tag to resolve.
        env["CLI_OPS_RELEASE_TAG"] = "local-wheels"
    config = build_setup_config_from_env(
        env,
        state=state,
        allowed_types=(DeploymentType.MULTI_NODE,),
        require_git_token=not wheels,
    )
    return config.model_copy(update={"ssh_user": topology_of(state).ssh_user})


def install_config(
    env: dict[str, str],
    state: ProjectState,
    *,
    prefix: str = "",
    target_host: str | None = None,
) -> SetupConfig:
    """Just enough to install base software on a node: the ECR key, and the
    GitHub token + tags (or local wheels). ``prefix`` selects a host's own
    keys, e.g. ``SANDBOX_`` for the sandbox's newer cli-ops."""
    topo = topology_of(state)
    wheels = wheel_vars(env, prefix=prefix)

    def pick(key: str) -> str:
        return ((env.get(f"{prefix}{key}") if prefix else None) or env.get(key) or "").strip()

    ecr_id, ecr_secret = pick("AWS_ACCESS_KEY_ID"), pick("AWS_SECRET_ACCESS_KEY")
    missing = [k for k, v in (("AWS_ACCESS_KEY_ID", ecr_id), ("AWS_SECRET_ACCESS_KEY", ecr_secret)) if not v]
    git_token = _get_git_token(env)
    if not wheels["cli_ops_wheel"] and not git_token:
        missing.append("GIT_TOKEN (or local wheels)")
    if missing:
        ui.error("Missing required variables in .env:")
        for k in missing:
            ui.muted(f"  - {prefix}{k}" if prefix and not k.startswith("GIT") else f"  - {k}")
        raise EnvSetupError()

    github_org = (env.get("GITHUB_ORG") or "iblai").strip()
    prod_images_repo = pick("PROD_IMAGES_REPO") or "iblai-prod-images"
    prod_images_tag = pick("PROD_IMAGES_TAG") or "main"
    cli_ops_tag = pick("CLI_OPS_RELEASE_TAG")
    if not cli_ops_tag and not wheels["cli_ops_wheel"]:
        repo, subdir = parse_repo_path(prod_images_repo)
        cli_ops_tag = resolve_pinned_cli_ops_tag(git_token, github_org, repo, prod_images_tag, subdir=subdir) or "main"

    return SetupConfig(
        ssh_private_key_path=state.config.ssh.private_key_path,
        ssh_user=topo.ssh_user,
        target_host=target_host or topo.hub,
        base_domain=state.config.dns.base_domain,
        cli_ops_release_tag=cli_ops_tag,
        prod_images_tag=prod_images_tag,
        aws_access_key_id=ecr_id,
        aws_secret_access_key=ecr_secret,
        aws_default_region=(env.get("AWS_DEFAULT_REGION") or state.config.region or "us-east-1").strip(),
        git_access_token=git_token,
        github_org=github_org,
        cli_ops_repo=pick("CLI_OPS_REPO") or "iblai-cli-ops",
        prod_images_repo=prod_images_repo,
    )


def sandbox_vars(env: dict[str, str], state: ProjectState) -> dict:
    """The sandbox connection's choices (`sandbox connect`)."""
    mentors = _hosts(env.get("SANDBOX_VM_MENTORS"))
    for pair in mentors:
        if ":" not in pair:
            raise _fail(
                f"SANDBOX_VM_MENTORS entry {pair!r} must be platform:mentor name.",
                hint="e.g. SANDBOX_VM_MENTORS=main:agentAI",
            )
    return {
        "sandbox_tenant": (env.get("SANDBOX_TENANT") or f"{state.name}-dm").strip(),
        "sandbox_dm_image": (env.get("SANDBOX_IMAGE") or "").strip(),
        "sandbox_graph_agents": parse_bool(env.get("SANDBOX_GRAPH_AGENTS"), default=True),
        "sandbox_vm_mentors": mentors,
        "sandbox_vm_egress": (env.get("SANDBOX_VM_EGRESS") or "none").strip(),
        "sandbox_egress_profiles": _hosts(env.get("SANDBOX_EGRESS_PROFILES")) or ["none"],
        "sandbox_max_sessions": int((env.get("SANDBOX_MAX_SESSIONS") or "20").strip() or 20),
    }
