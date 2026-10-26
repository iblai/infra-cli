"""Per-role commands for multi-node deployments on existing hosts.

    iblai infra app init       -f .env   # 1. hub: base software, store endpoints, secrets
    iblai infra stores setup   -f .env   # 2. stores node from the hub's root
    iblai infra app setup      -f .env   # 3. hub: migrations, launch, one-time jobs; beat
    iblai infra app add        -f .env --host <ip>   # 4. more app nodes, cloned
    iblai infra sandbox setup  -f .env   # 5. KVM sandbox server
    iblai infra sandbox connect -f .env  # 6. DM <-> sandbox, every app node
    iblai infra call connect   -f .env --call-project <name>   # 7. DM <-> LiveKit
    iblai infra app sync       -f .env   # hub config -> every node, rolling
    iblai infra app verify     -f .env   # containers, health, secrets, endpoints

Every command reads the same .env keys (`.env.multinode.example`). The
project's state records hosts, NODE_IDs and the store layout; credentials
are read from the .env on each run and never written down.
"""

from __future__ import annotations

from pathlib import Path

import typer

from iblai_infra import ui
from iblai_infra.ansible import multinode as mn
from iblai_infra.env_utils import load_env_file

app_node_app = typer.Typer(
    name="app",
    help="Multi-node: set up the hub and app nodes, sync config, verify",
    no_args_is_help=True,
)
stores_app = typer.Typer(
    name="stores",
    help="Multi-node: set up the stores node (MongoDB, Meilisearch, ClickHouse, MinIO, beat, ...)",
    no_args_is_help=True,
)
sandbox_app = typer.Typer(
    name="sandbox",
    help="Multi-node: set up a KVM sandbox server and connect the DM to it",
    no_args_is_help=True,
)
call_app = typer.Typer(
    name="call",
    help="Multi-node: connect the DM to a LiveKit call server",
    no_args_is_help=True,
)

ENV_OPTION = typer.Option(
    Path(".env"), "--env-file", "-f", help="Path to the multi-node .env file"
)

# Printed after each step: what only the operator can do in their cloud.
CLOUD_CHECKLIST: dict[str, list[str]] = {
    "app_init": [
        "Load balancer target group health check: HTTP :80 /heartbeat with Host learn.<domain> (a plain / gets nginx's 301).",
        "App nodes join the target group only once `app setup` / `app add` reports them healthy.",
    ],
    "stores_setup": [
        "Stores node security group: the store ports (7700, 8123, 9000, 9090, 27017, plus 3306/5432/6379/6380 for local MySQL/Postgres/Redis) from the app nodes only.",
        "Managed stores (RDS, ElastiCache): their security groups admit the app nodes and the stores node (beat).",
    ],
    "sandbox_setup": [
        "Load balancer: a host rule for the sandbox domain to this server on :80, health check /healthcheck.",
        "Load balancer idle timeout 660 s (long execs); the app nodes' nginx keep-alive is already 700 s.",
        "Sandbox security group: :80 from the load balancer only; 8000/3000 closed.",
    ],
}


def _env(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        ui.error(f"No .env file found at: {env_file}")
        ui.muted("  Copy [brand].env.multinode.example[/brand], fill it in, and pass it with -f.")
        raise typer.Exit(1)
    return load_env_file(env_file)


def _checklist(step: str) -> None:
    items = CLOUD_CHECKLIST.get(step)
    if not items:
        return
    ui.newline()
    ui.info("In your cloud account (not done by this command):")
    for item in items:
        ui.muted(f"  - {item}")


def _run(
    runner: mn.MultiNodeRunner,
    description: str,
    *,
    completes_setup: bool = False,
    step: str = "",
    next_hint: str = "",
) -> None:
    if not runner.preflight():
        raise typer.Exit(1)
    runner.setup()
    ok = runner.run_step(description, completes_setup=completes_setup)
    ui.newline()
    if not ok:
        ui.error(f"{description} failed. Fix the issue and re-run this command; every step is safe to repeat.")
        raise typer.Exit(1)
    ui.success(f"{description}: done.")
    _checklist(step)
    if next_hint:
        ui.newline()
        ui.info(f"Next: [brand]{next_hint}[/brand]")
    ui.newline()


def _common_vars(env: dict[str, str], state) -> dict:
    from iblai_infra.env_multinode import platform_vars, topology_of, topology_vars

    return {**topology_vars(topology_of(state)), **platform_vars(env)}


# ---------------------------------------------------------------------------
# iblai infra app ...
# ---------------------------------------------------------------------------

@app_node_app.command("init")
def app_init(env_file: Path = ENV_OPTION) -> None:
    """Step 1: the hub's base software and config root (no services yet)."""
    from iblai_infra.env_multinode import (
        load_or_create_state,
        platform_config,
        store_secret_vars,
        topology_of,
        wheel_vars,
    )

    env = _env(env_file)
    ui.private_access_notice()
    state = load_or_create_state(env, create=True)
    topo = topology_of(state)
    config = platform_config(env, state)
    run_vars = {**_common_vars(env, state), **wheel_vars(env), **store_secret_vars(env, topo)}
    runner = mn.MultiNodeRunner(
        state, config, topo, "app_init.yml", mn.APP_INIT_LABELS,
        run_vars=run_vars, ssh_groups=("hub", "stores"),
    )
    _run(runner, "Hub configuration", step="app_init", next_hint=f"iblai infra stores setup -f {env_file}")


@app_node_app.command("setup")
def app_setup(env_file: Path = ENV_OPTION) -> None:
    """Step 3: launch the platform on the hub (one-time jobs), then beat."""
    from iblai_infra.env_multinode import (
        load_or_create_state,
        platform_config,
        store_secret_vars,
        topology_of,
        wheel_vars,
    )

    env = _env(env_file)
    state = load_or_create_state(env)
    topo = topology_of(state)
    config = platform_config(env, state)
    run_vars = {**_common_vars(env, state), **wheel_vars(env), **store_secret_vars(env, topo)}
    runner = mn.MultiNodeRunner(
        state, config, topo, "app_setup.yml", mn.APP_SETUP_LABELS,
        run_vars=run_vars, ssh_groups=("hub", "stores"),
    )
    _run(
        runner, "Platform launch on the hub", completes_setup=True,
        next_hint=f"iblai infra app add -f {env_file} --host <ip>" if not topo.apps
        else f"iblai infra app add -f {env_file} --host {topo.apps[0]}",
    )


@app_node_app.command("add")
def app_add(
    env_file: Path = ENV_OPTION,
    host: list[str] = typer.Option(..., "--host", help="App node to add (repeatable)"),
) -> None:
    """Step 4: clone the hub onto more app nodes. No one-time jobs run there."""
    from iblai_infra.env_multinode import install_config, load_or_create_state, save_topology, topology_of, wheel_vars

    env = _env(env_file)
    state = load_or_create_state(env)
    if state.setup_status != "completed":
        ui.error("Run [brand]iblai infra app setup[/brand] on the hub first.")
        raise typer.Exit(1)
    topo = topology_of(state)
    for h in host:
        if h in (topo.hub, topo.stores, topo.sandbox):
            ui.error(f"{h} already holds another role in this deployment.")
            raise typer.Exit(1)
    config = install_config(env, state)
    run_vars = {**_common_vars(env, state), **wheel_vars(env)}
    pending = [h for h in host if h not in topo.apps]
    topo.apps.extend(pending)
    topo.assign_node_ids()
    runner = mn.MultiNodeRunner(
        state, config, topo, "app_add.yml", mn.APP_ADD_LABELS,
        run_vars=run_vars, new_apps=list(host),
        ssh_groups=("hub", "new_apps", "stores"), fact_groups=("hub", "apps", "stores"),
    )
    if not runner.preflight():
        raise typer.Exit(1)
    # Recorded once the node is reachable, so a typo doesn't join the topology.
    save_topology(state, topo)
    runner.setup()
    ok = runner.run_step("App node clone")
    ui.newline()
    if not ok:
        ui.error("App node clone failed. Fix the issue and re-run; it is safe to repeat.")
        raise typer.Exit(1)
    ui.success(f"App node(s) {', '.join(host)} are up. Register them with the load balancer.")
    ui.newline()


@app_node_app.command("sync")
def app_sync(
    env_file: Path = ENV_OPTION,
    restart_edx: bool = typer.Option(False, "--restart-edx", help="Also restart edX (for edX settings changes)"),
) -> None:
    """Copy the hub's config to every node and apply it, one app node at a time."""
    from iblai_infra.env_multinode import load_or_create_state, topology_of
    from iblai_infra.models import SetupConfig

    env = _env(env_file)
    state = load_or_create_state(env)
    topo = topology_of(state)
    config = SetupConfig.for_feature(state, ssh_user=topo.ssh_user, restart_services=restart_edx)
    runner = mn.MultiNodeRunner(
        state, config, topo, "sync.yml", mn.SYNC_LABELS,
        run_vars=_common_vars(env, state), ssh_groups=("hub", "apps", "stores"),
    )
    _run(runner, "Config sync")


@app_node_app.command("verify")
def app_verify(env_file: Path = ENV_OPTION) -> None:
    """Check every node: containers, local health, identical secrets, public endpoints."""
    from iblai_infra.env_multinode import load_or_create_state, topology_of
    from iblai_infra.models import SetupConfig

    env = _env(env_file)
    state = load_or_create_state(env)
    topo = topology_of(state)
    config = SetupConfig.for_feature(state, ssh_user=topo.ssh_user)
    runner = mn.MultiNodeRunner(
        state, config, topo, "verify.yml", mn.VERIFY_LABELS,
        run_vars=_common_vars(env, state), ssh_groups=("hub", "apps", "stores"),
    )
    _run(runner, "Deployment check")


# ---------------------------------------------------------------------------
# iblai infra stores setup
# ---------------------------------------------------------------------------

@stores_app.command("setup")
def stores_setup(env_file: Path = ENV_OPTION) -> None:
    """Step 2: the stores node, from a copy of the hub's config root."""
    from iblai_infra.env_multinode import install_config, load_or_create_state, topology_of, wheel_vars

    env = _env(env_file)
    state = load_or_create_state(env)
    topo = topology_of(state)
    config = install_config(env, state, target_host=topo.stores)
    runner = mn.MultiNodeRunner(
        state, config, topo, "stores_setup.yml", mn.STORES_SETUP_LABELS,
        run_vars={**_common_vars(env, state), **wheel_vars(env)},
        ssh_groups=("hub", "stores"), fact_groups=("hub", "apps", "stores"),
    )
    _run(runner, "Stores node setup", step="stores_setup", next_hint=f"iblai infra app setup -f {env_file}")


# ---------------------------------------------------------------------------
# iblai infra sandbox ...
# ---------------------------------------------------------------------------

@sandbox_app.command("setup")
def sandbox_setup(env_file: Path = ENV_OPTION) -> None:
    """Step 5: a KVM sandbox server (iblai-sandbox), behind nginx on :80."""
    from iblai_infra.env_multinode import (
        install_config,
        load_or_create_state,
        sandbox_vars,
        topology_of,
        wheel_vars,
    )

    env = _env(env_file)
    state = load_or_create_state(env)
    topo = topology_of(state)
    if not topo.sandbox:
        ui.error("Set SANDBOX_HOST in the .env.")
        raise typer.Exit(1)
    config = install_config(env, state, prefix="SANDBOX_", target_host=topo.sandbox)
    run_vars = {**_common_vars(env, state), **wheel_vars(env, prefix="SANDBOX_"), **sandbox_vars(env, state)}
    runner = mn.MultiNodeRunner(
        state, config, topo, "sandbox_setup.yml", mn.SANDBOX_SETUP_LABELS,
        run_vars=run_vars, ssh_groups=("sandbox",),
    )
    _run(runner, "Sandbox server setup", step="sandbox_setup", next_hint=f"iblai infra sandbox connect -f {env_file}")


@sandbox_app.command("connect")
def sandbox_connect(env_file: Path = ENV_OPTION) -> None:
    """Step 6: a tenant and key for the DM, the DM's sandbox settings on every node."""
    from iblai_infra.env_multinode import load_or_create_state, sandbox_vars, topology_of
    from iblai_infra.models import SetupConfig

    env = _env(env_file)
    state = load_or_create_state(env)
    topo = topology_of(state)
    if not topo.sandbox:
        ui.error("This deployment has no sandbox host. Set SANDBOX_HOST and run `iblai infra sandbox setup`.")
        raise typer.Exit(1)
    if state.setup_status != "completed":
        ui.error("Run [brand]iblai infra app setup[/brand] first: the DM must be running.")
        raise typer.Exit(1)
    config = SetupConfig.for_feature(state, ssh_user=topo.ssh_user)
    runner = mn.MultiNodeRunner(
        state, config, topo, "sandbox_connect.yml", mn.SANDBOX_CONNECT_LABELS,
        run_vars={**_common_vars(env, state), **sandbox_vars(env, state)},
        ssh_groups=("hub", "apps", "stores", "sandbox"),
    )
    _run(runner, "DM connected to the sandbox")


# ---------------------------------------------------------------------------
# iblai infra call connect
# ---------------------------------------------------------------------------

@call_app.command("connect")
def call_connect(
    env_file: Path = ENV_OPTION,
    call_project: str = typer.Option(
        "", "--call-project",
        help="The call server's infra-cli project (else CALL_HOST + CALL_SSH_PRIVATE_KEY_PATH from the .env)",
    ),
) -> None:
    """Step 7: give every DM the call server's LiveKit credentials; AI calls on."""
    from iblai_infra.env_multinode import load_or_create_state, topology_of
    from iblai_infra.models import DeploymentType, SetupConfig
    from iblai_infra.terraform.state import load_state

    env = _env(env_file)
    state = load_or_create_state(env)
    topo = topology_of(state)
    if state.setup_status != "completed":
        ui.error("Run [brand]iblai infra app setup[/brand] first: the DM must be running.")
        raise typer.Exit(1)

    if call_project:
        call_state = load_state(call_project)
        if call_state is None or getattr(call_state.config, "deployment_type", None) != DeploymentType.CALL:
            ui.error(f"'{call_project}' is not a call-server project.")
            raise typer.Exit(1)
        call_host = (call_state.outputs or {}).get("instance_public_ip", "")
        call_key = call_state.config.ssh.private_key_path
    else:
        call_host = (env.get("CALL_HOST") or "").strip()
        raw_key = (env.get("CALL_SSH_PRIVATE_KEY_PATH") or env.get("SSH_PRIVATE_KEY_PATH") or "").strip()
        call_key = Path(raw_key).expanduser() if raw_key else None
    if not call_host or call_key is None or not Path(call_key).exists():
        ui.error("Pass --call-project, or set CALL_HOST and CALL_SSH_PRIVATE_KEY_PATH in the .env.")
        raise typer.Exit(1)

    config = SetupConfig.for_feature(state, ssh_user=topo.ssh_user)
    runner = mn.MultiNodeRunner(
        state, config, topo, "call_connect.yml", mn.CALL_CONNECT_LABELS,
        run_vars=_common_vars(env, state),
        extra_hosts=[mn.ExtraHost(call_host, "call", Path(call_key), (env.get("CALL_SSH_USER") or "ubuntu").strip())],
        ssh_groups=("hub", "apps", "stores"),
    )
    _run(runner, "DM connected to the call server")
    ui.muted("  AI calls connect once wss://call.<domain> resolves to the call server and its ports are open.")
    ui.newline()
