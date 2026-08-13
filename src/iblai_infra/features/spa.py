"""``iblai infra spa`` — clone a deployed SPA into a second instance.

Runs a copy of an existing SPA alongside the original on the same VM, on its
own port and its own domain, so it can be customised without touching the one
the platform depends on. The clone copies the source's *running* environment
file rather than re-rendering it from config, so it starts identical to what
the source is actually serving, including anything hand-edited on the box.

Stock SPAs occupy ports 5000-5009; clones are allocated from 5060 upwards.
Their nginx blocks live in /etc/nginx/conf.d/custom_domains/, which the
platform's proxy sync excludes, so they survive `ibl global-proxy` runs.
"""

from __future__ import annotations

import re

import typer

from iblai_infra import ui
from iblai_infra.features._common import (
    apply_feature,
    load_feature_target,
    prompt_required,
)
from iblai_infra.models import ProjectState, SetupConfig

spa_app = typer.Typer(
    name="spa",
    help="Clone and manage additional SPA instances on an existing environment",
    no_args_is_help=True,
)

CLONE_TAGS = ["spa_clone"]
CLONE_LABELS = {"spa_clone": "SPA Clone"}

REMOVE_TAGS = ["spa_remove"]
REMOVE_LABELS = {"spa_clone_remove": "SPA Removal"}

SPA_DIR = "/ibl/app/ibl-spa"

# Where clone ports start. The stock SPAs sit at 5000-5009; leaving a gap keeps
# room for new ones upstream without colliding with anything created here.
CLONE_PORT_BASE = 5060

# The SPAs the platform ships and depends on. Refusing these as a removal
# target is what stops `spa remove` taking down the real mentor or auth.
STOCK_SPAS = frozenset(
    {"auth", "mentor", "os", "lms", "skills", "hq", "vibe", "courseai", "videoai"}
)

VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# ---------------------------------------------------------------------------
# Remote discovery
# ---------------------------------------------------------------------------

def discover_spas(state: ProjectState) -> dict[str, int] | None:
    """Return {spa_name: published_port} for everything deployed. None if unreachable.

    Read off the server because that is the only place the truth lives - a
    clone made last month is not recorded anywhere locally.
    """
    from iblai_infra.ansible.runner import AnsibleRunner

    runner = AnsibleRunner(state, SetupConfig.for_feature(state))
    script = (
        f'for d in {SPA_DIR}/*/; do '
        'n=$(basename "$d"); '
        'p=$(grep -oE \'"[0-9]{4,5}:[0-9]{4,5}"\' "$d/docker-compose.yml" 2>/dev/null '
        '| head -1 | tr -d \'"\' | cut -d: -f1); '
        'echo "$n=$p"; done'
    )
    out = runner.run_remote_script(script)
    if out is None:
        return None

    found: dict[str, int] = {}
    for line in out.splitlines():
        name, _, port = line.strip().partition("=")
        if name:
            found[name] = int(port) if port.isdigit() else 0
    return found


def next_free_port(used: dict[str, int]) -> int:
    """First unused port at or above the clone base."""
    taken = set(used.values())
    port = CLONE_PORT_BASE
    while port in taken:
        port += 1
    return port


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------

@spa_app.command("clone")
def spa_clone(
    name: str = typer.Argument(help="Environment name"),
    source: str = typer.Option(None, "--from", help="SPA to clone (skips the prompt)"),
    new_name: str = typer.Option(None, "--as", help="Name for the clone (skips the prompt)"),
    domain: str = typer.Option(None, "--domain", help="Domain to serve it at (skips the prompt)"),
    port: int = typer.Option(None, "--port", help="Port to use (default: next free from 5060)"),
) -> None:
    """Clone a deployed SPA into a second instance on its own domain."""
    import questionary

    state = load_feature_target(name)

    ui.newline()
    ui.console.rule("[brand]Clone a SPA[/brand]")

    deployed = discover_spas(state)
    if deployed is None:
        ui.error("Could not reach the server to see what is deployed.")
        raise typer.Exit(1)
    if not deployed:
        ui.error(f"No SPAs found at {SPA_DIR} on this environment.")
        raise typer.Exit(1)

    # ----- source -----
    if source is None:
        choices = [
            questionary.Choice(f"{n:<12} port {p or '?'}", value=n)
            for n, p in sorted(deployed.items())
        ]
        source = questionary.select(
            "Which SPA do you want to clone?",
            choices=choices,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if source is None:
            ui.abort()

    if source not in deployed:
        ui.error(f"'{source}' is not deployed on this environment.")
        ui.muted(f"  Available: {', '.join(sorted(deployed))}")
        raise typer.Exit(1)

    source_port = deployed[source]
    if not source_port:
        ui.error(f"Could not read the published port for '{source}'.")
        raise typer.Exit(1)

    # ----- name -----
    if new_name is None:
        new_name = prompt_required(
            "Name for the clone:",
            default=f"{source}-custom",
            validate=lambda v: bool(VALID_NAME.match(v.strip().lower()))
            or "Lowercase letters, numbers, hyphens and underscores",
        )
    new_name = new_name.strip().lower()

    if not VALID_NAME.match(new_name):
        ui.error(f"'{new_name}' is not a valid name.")
        raise typer.Exit(1)
    if new_name in deployed:
        ui.error(f"'{new_name}' already exists on this environment.")
        raise typer.Exit(1)

    # ----- domain -----
    if domain is None:
        domain = prompt_required(
            "Domain to serve it at:",
            default=f"{new_name}.{state.config.dns.base_domain}",
            validate=lambda v: ("." in v.strip() and " " not in v.strip())
            or "Enter a hostname, e.g. custom.example.com",
        )
    domain = domain.strip().lower().replace("https://", "").replace("http://", "").rstrip("/")

    # ----- port -----
    if port is None:
        port = next_free_port(deployed)
    elif port in deployed.values():
        ui.error(f"Port {port} is already used on this environment.")
        raise typer.Exit(1)

    # ----- confirm -----
    ui.newline()
    ui.summary_panel(
        "Clone",
        [
            ("Source", f"{source} (port {source_port})"),
            ("New SPA", new_name),
            ("Port", str(port)),
            ("Domain", f"http://{domain}"),
            ("Directory", f"{SPA_DIR}/{new_name}"),
        ],
    )
    ui.muted(f"  The clone starts with a copy of {source}'s environment file.")
    ui.muted(f"  Edit {SPA_DIR}/{new_name}/.env.{new_name} afterwards to customise it.")
    ui.newline()

    if not questionary.confirm(
        "Create it?", default=True, style=ui.PROMPT_STYLE, qmark=ui.QMARK
    ).ask():
        ui.abort()

    config = SetupConfig.for_feature(state)
    ok = apply_feature(
        state,
        config,
        CLONE_TAGS,
        CLONE_LABELS,
        description=f"Cloning {source} to {new_name}",
        extra_vars={
            "spa_clone_enabled": True,
            "spa_clone_source": source,
            "spa_clone_source_port": source_port,
            "spa_clone_name": new_name,
            "spa_clone_port": port,
            "spa_clone_domain": domain,
        },
    )

    ui.newline()
    if not ok:
        ui.error(f"Could not clone {source}.")
        raise typer.Exit(1)

    ui.success(f"[highlight]{new_name}[/highlight] is running on port {port}.")
    ui.newline()
    ui.muted(f"  Point DNS for [highlight]{domain}[/highlight] at this server, then it is reachable.")
    ui.muted(f"  Customise it by editing {SPA_DIR}/{new_name}/.env.{new_name}")
    ui.muted(f"  and restarting: cd {SPA_DIR}/{new_name} && docker compose up -d")
    ui.newline()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@spa_app.command("list")
def spa_list(name: str = typer.Argument(help="Environment name")) -> None:
    """Show the SPAs deployed on an environment and the ports they hold."""
    state = load_feature_target(name)

    deployed = discover_spas(state)
    if deployed is None:
        ui.error("Could not reach the server.")
        raise typer.Exit(1)

    ui.newline()
    ui.console.rule("[brand]Deployed SPAs[/brand]")
    if not deployed:
        ui.muted(f"  Nothing found at {SPA_DIR}.")
        ui.newline()
        return

    width = max(len(n) for n in deployed)
    for spa_name, spa_port in sorted(deployed.items(), key=lambda kv: (kv[1], kv[0])):
        kind = "[muted]stock[/muted]" if spa_name in STOCK_SPAS else "[success]clone[/success]"
        ui.console.print(f"  {spa_name:<{width}}  port {spa_port or '?':<6} {kind}")
    ui.newline()


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

@spa_app.command("remove")
def spa_remove(
    name: str = typer.Argument(help="Environment name"),
    spa: str = typer.Option(..., "--spa", help="Name of the cloned SPA to remove"),
    assume_yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
) -> None:
    """Remove a cloned SPA — its containers, nginx block and directory."""
    import questionary

    state = load_feature_target(name)
    spa = spa.strip().lower()

    # The whole point of the guard: this role deletes a directory and reloads
    # nginx. Pointed at a stock SPA it would take the platform down.
    if spa in STOCK_SPAS:
        ui.error(f"'{spa}' is one of the platform's own SPAs and cannot be removed here.")
        ui.muted("  Only clones created with [brand]iblai infra spa clone[/brand] can be removed.")
        raise typer.Exit(1)

    if not assume_yes:
        ui.warning(f"This deletes {SPA_DIR}/{spa} and its nginx block on '{name}'.")
        if not questionary.confirm(
            "Continue?", default=False, style=ui.PROMPT_STYLE, qmark=ui.QMARK
        ).ask():
            ui.abort()

    config = SetupConfig.for_feature(state)
    ok = apply_feature(
        state,
        config,
        REMOVE_TAGS,
        REMOVE_LABELS,
        description=f"Removing {spa}",
        extra_vars={"spa_remove_enabled": True, "spa_remove_name": spa},
    )

    ui.newline()
    if not ok:
        ui.error(f"Could not remove {spa}.")
        raise typer.Exit(1)
    ui.success(f"[highlight]{spa}[/highlight] removed from {name}.")
    ui.newline()
