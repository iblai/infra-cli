"""``iblai infra waf`` — post-provision WAF toggle subgroup.

Lets operators add, update, remove, or inspect AWS WAFv2 protection on an
already-provisioned single-server stack without re-running the provisioning
wizard. Re-uses every piece of the provision-time WAF wiring:

- ``WAFConfig`` (model + IP normalisation/validation)
- ``_prompt_waf_ips`` (interactive IP prompt with pre-fill support)
- ``_validate_ip_csv`` (questionary validator)
- ``TerraformRunner._generate_tfvars`` (emits ``enable_waf`` +
  ``waf_allowed_ips`` for single-server)
- ``TerraformRunner.reapply`` (re-runs terraform on the existing workspace)
- The ``waf.tf`` template + ``waf_web_acl_arn`` / ``waf_ip_set_arn`` outputs
"""

from __future__ import annotations

from pathlib import Path

import questionary
import typer
from rich.table import Table

from iblai_infra import ui
from iblai_infra.env_utils import load_env_file
from iblai_infra.models import CloudProvider, DeploymentType, ProjectState, WAFConfig
from iblai_infra.prompts.dns_certs import _prompt_waf_ips
from iblai_infra.terraform.runner import TerraformRunner
from iblai_infra.terraform.state import list_all_states, load_state, save_state

waf_app = typer.Typer(
    name="waf",
    help="Manage AWS WAFv2 protection on an existing single-server stack.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Shared guards & helpers
# ---------------------------------------------------------------------------


def _eligible_states() -> list[ProjectState]:
    """Return projects WAF can target — single-server, created, terraform-backed."""
    out: list[ProjectState] = []
    for s in list_all_states():
        if s.status != "created":
            continue
        if s.provider == "bootstrap":
            continue
        if s.config.cloud != CloudProvider.AWS:  # AWS WAFv2 only
            continue
        if s.config.deployment_type != DeploymentType.SINGLE:
            continue
        out.append(s)
    return out


def _resolve_project_name(name: str | None) -> str:
    """If ``name`` is None, present an interactive picker of eligible projects."""
    if name:
        return name
    eligible = _eligible_states()
    if not eligible:
        ui.error("No WAF-eligible projects found.")
        ui.muted(
            "WAF requires a single-server stack in status 'created'. "
            "Provision one with [brand]iblai infra provision[/brand]."
        )
        raise typer.Exit(1)
    if len(eligible) == 1:
        return eligible[0].name
    choices = [
        questionary.Choice(
            f"{s.name} ({s.config.dns.base_domain})", value=s.name,
        )
        for s in eligible
    ]
    selected = questionary.select(
        "Which project?",
        choices=choices,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if selected is None:
        ui.abort()
    return selected


def _load_and_guard_waf_target(name: str) -> ProjectState:
    """Load ``name`` and bail with a clear message if it can't host WAF.

    Guards on:
        - project must exist
        - terraform-managed (provider != "bootstrap")
        - single-server deployment
        - status == "created"
        - workspace_path + main.tf must exist on disk
    """
    state = load_state(name)
    if state is None:
        ui.error(f"No project found with name: {name}")
        ui.muted("Run [brand]iblai infra list[/brand] to see available projects.")
        raise typer.Exit(1)

    if state.provider == "bootstrap":
        ui.error(
            f"Project '{name}' is a bootstrap project (no Terraform workspace)."
        )
        ui.muted("WAF requires a stack provisioned via Terraform.")
        raise typer.Exit(1)

    if state.config.cloud != CloudProvider.AWS:
        ui.error(
            f"Project '{name}' targets {state.config.cloud.value.upper()}; "
            "WAF (AWS WAFv2) is only available on AWS stacks."
        )
        ui.muted("Cloud Armor support for GCP is a planned follow-up.")
        raise typer.Exit(1)

    if state.config.deployment_type != DeploymentType.SINGLE:
        ui.error(
            f"Project '{name}' is {state.config.deployment_type.value}; "
            "WAF post-provision toggling is only supported for single-server."
        )
        raise typer.Exit(1)

    if state.status != "created":
        ui.error(
            f"Project '{name}' has status '{state.status}'; "
            "WAF can only be toggled on stacks in status 'created'."
        )
        raise typer.Exit(1)

    ws = Path(state.workspace_path)
    if not ws.exists() or not (ws / "main.tf").exists():
        ui.error(f"Terraform workspace missing at {ws}.")
        ui.muted(
            "The state file references a workspace that no longer exists. "
            "Re-run [brand]iblai infra provision[/brand] to re-create it."
        )
        raise typer.Exit(1)

    return state


def _apply_state_to_terraform(state: ProjectState) -> dict:
    """Run :meth:`TerraformRunner.reapply` and merge new outputs into state."""
    runner = TerraformRunner(state.config)
    runner.ws = Path(state.workspace_path)
    runner.state = state
    outputs = runner.reapply()
    if outputs:
        state.outputs = {**(state.outputs or {}), **outputs}
        save_state(state)
    return outputs


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@waf_app.command("enable")
def waf_enable(
    name: str = typer.Argument(
        None,
        help="Project name. Omit to pick interactively from eligible projects.",
    ),
) -> None:
    """Enable WAF on a single-server stack, or update the existing allowlist.

    If WAF is already enabled, prompts to update the IP allowlist (current
    list is pre-filled for easy edit). Otherwise prompts for a fresh list.
    Triggers a Terraform apply on the existing workspace.
    """
    project = _resolve_project_name(name)
    state = _load_and_guard_waf_target(project)

    current = (
        state.config.waf.allowed_ips
        if state.config.waf and state.config.waf.enabled
        else []
    )

    if current:
        ui.info(
            f"WAF is already enabled for [highlight]{project}[/highlight] "
            f"with [highlight]{len(current)}[/highlight] IP/CIDR."
        )
        proceed = questionary.confirm(
            "Update the allowlist?",
            default=True,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if proceed is None or not proceed:
            ui.muted("No changes made.")
            raise typer.Exit(0)
    else:
        ui.info(
            f"Enabling WAF on [highlight]{project}[/highlight] "
            f"(domain: {state.config.dns.base_domain})."
        )

    tokens = _prompt_waf_ips(default=current)
    try:
        new_waf = WAFConfig(enabled=True, allowed_ips=tokens)
    except ValueError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)

    _show_summary(state, new_waf, action="enable")

    confirm = questionary.confirm(
        "Apply this change?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if confirm is None or not confirm:
        ui.abort("Cancelled. No infrastructure changes made.")

    state.config.waf = new_waf
    save_state(state)

    outputs = _apply_state_to_terraform(state)
    _print_post_apply(state, outputs, action="enabled")


@waf_app.command("enable-env")
def waf_enable_env(
    name: str = typer.Argument(
        None,
        help="Project name. Omit to pick interactively from eligible projects.",
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-f",
        help="Path to .env file (default: .env in current directory)",
    ),
) -> None:
    """Non-interactive WAF enable / allowlist update from a .env file.

    Reads ``WAF_ALLOWED_IPS`` (required, comma-separated IPs/CIDRs). The
    fact that this command was invoked is the enable signal; the value of
    ``ENABLE_WAF`` in the .env is ignored. Bare IPs are auto-suffixed
    with /32 by the ``WAFConfig`` validator.
    """
    if not env_file.exists():
        ui.error(f"No .env file found at: {env_file}")
        raise typer.Exit(1)

    env = load_env_file(env_file)
    raw = (env.get("WAF_ALLOWED_IPS") or "").strip()
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        ui.error("WAF_ALLOWED_IPS is required in .env for `waf enable-env`.")
        ui.muted("Example: WAF_ALLOWED_IPS=203.0.113.7,10.0.0.0/16")
        raise typer.Exit(1)

    project = _resolve_project_name(name)
    state = _load_and_guard_waf_target(project)

    try:
        new_waf = WAFConfig(enabled=True, allowed_ips=tokens)
    except ValueError as exc:
        ui.error(f"Invalid WAF_ALLOWED_IPS: {exc}")
        raise typer.Exit(1)

    ui.info(
        f"Enabling WAF on [highlight]{project}[/highlight] "
        f"with [highlight]{len(new_waf.allowed_ips)}[/highlight] IP/CIDR."
    )

    state.config.waf = new_waf
    save_state(state)

    outputs = _apply_state_to_terraform(state)
    _print_post_apply(state, outputs, action="enabled")


@waf_app.command("disable")
def waf_disable(
    name: str = typer.Argument(help="Project name"),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip the confirmation prompt (CI flag).",
    ),
) -> None:
    """Disable WAF on a single-server stack.

    Removes the Web ACL, IPSet, and association. The ALB stays intact and
    serves traffic unfiltered. By default asks for Y/N confirmation;
    ``--yes`` skips for scripted use.
    """
    state = _load_and_guard_waf_target(name)

    if not (state.config.waf and state.config.waf.enabled):
        ui.info(f"WAF is already disabled for [highlight]{name}[/highlight].")
        raise typer.Exit(0)

    if not yes:
        confirm = questionary.confirm(
            f"Disable WAF on {name}? This removes the Web ACL, IPSet, and "
            "association. The ALB will serve traffic unfiltered.",
            default=False,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if confirm is None or not confirm:
            ui.abort("Cancelled.")

    state.config.waf = WAFConfig(enabled=False)
    save_state(state)

    outputs = _apply_state_to_terraform(state)

    # Clear stale WAF output keys so subsequent `iblai infra status` /
    # `waf status` don't show ARNs that no longer exist. The terraform
    # outputs themselves now return "" for these (the template uses a
    # conditional), but we drop the keys for cleanliness.
    if state.outputs:
        state.outputs.pop("waf_web_acl_arn", None)
        state.outputs.pop("waf_ip_set_arn", None)
        save_state(state)

    _print_post_apply(state, outputs, action="disabled")


@waf_app.command("status")
def waf_status(
    name: str = typer.Argument(
        None,
        help="Project name. Omit to list WAF state for all eligible projects.",
    ),
) -> None:
    """Show WAF state for one project (detail view) or all (table view)."""
    if name:
        state = load_state(name)
        if state is None:
            ui.error(f"No project found with name: {name}")
            raise typer.Exit(1)
        _print_one_status(state)
        return

    eligible = _eligible_states()
    if not eligible:
        ui.info("No single-server projects in status 'created' found.")
        return
    _print_status_table(eligible)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _show_summary(state: ProjectState, new_waf: WAFConfig, *, action: str) -> None:
    """Pre-apply summary panel for the interactive ``enable`` flow."""
    rows: list[tuple[str, str]] = [
        ("Project", state.name),
        ("Domain", state.config.dns.base_domain),
        ("Action", "Enable / update WAF"),
        ("IPs/CIDRs", str(len(new_waf.allowed_ips))),
    ]
    for ip in new_waf.allowed_ips:
        rows.append(("  ", ip))
    ui.summary_panel("WAF Change Summary", rows)


def _print_post_apply(state: ProjectState, outputs: dict, *, action: str) -> None:
    ui.newline()
    ui.success(f"WAF {action} on [highlight]{state.name}[/highlight].")
    arn = (outputs or {}).get("waf_web_acl_arn") or ""
    if arn:
        ui.info(f"Web ACL ARN: [highlight]{arn}[/highlight]")
    ui.newline()


def _print_one_status(state: ProjectState) -> None:
    waf = state.config.waf
    enabled = bool(waf and waf.enabled)
    rows: list[tuple[str, str]] = [
        ("Project", state.name),
        ("Deployment", state.config.deployment_type.value),
        ("Domain", state.config.dns.base_domain),
        ("Status", state.status),
        ("WAF", "Enabled" if enabled else "Disabled"),
    ]
    if enabled and waf:
        rows.append(("Allowlist size", str(len(waf.allowed_ips))))
        for ip in waf.allowed_ips:
            rows.append(("  ", ip))
        if state.outputs:
            arn = state.outputs.get("waf_web_acl_arn")
            if arn:
                rows.append(("Web ACL ARN", arn))
            ip_arn = state.outputs.get("waf_ip_set_arn")
            if ip_arn:
                rows.append(("IPSet ARN", ip_arn))
    ui.summary_panel("WAF Status", rows)


def _print_status_table(states: list[ProjectState]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Project")
    table.add_column("Domain")
    table.add_column("Deployment")
    table.add_column("WAF")
    table.add_column("Allowlist", justify="right")
    for s in states:
        waf = s.config.waf
        enabled = bool(waf and waf.enabled)
        table.add_row(
            s.name,
            s.config.dns.base_domain,
            s.config.deployment_type.value,
            "Enabled" if enabled else "Disabled",
            str(len(waf.allowed_ips)) if enabled and waf else "—",
        )
    ui.console.print(table)
