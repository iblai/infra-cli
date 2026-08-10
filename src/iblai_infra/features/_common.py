"""Shared plumbing for the post-setup feature commands.

Each ``iblai infra <feature>`` subgroup adds one optional integration to an
environment that is already provisioned and bootstrapped, by re-running a
single tagged Ansible role. The pieces they all need live here.

Whether a feature needs the services restarted afterwards depends on how the
platform reads the value:

* Written into ``config.yml`` and consumed as a container environment
  variable - SMTP - only reaches a running service once the containers are
  recreated, so those commands ask before restarting.
* Written as a database row that Django reads per request - Google SSO,
  Stripe, the LLM credential - is live the moment the role finishes.
* Microsoft SSO patches edX settings, so its role restarts edX itself when
  the configuration actually changed. The command warns rather than offering
  a choice, because skipping it would leave the setting inert.
"""

from __future__ import annotations

import typer

from iblai_infra import ui
from iblai_infra.models import ProjectState, SetupConfig


def load_feature_target(name: str) -> ProjectState:
    """Load ``name``, refusing anything that can't take a post-setup change.

    Optional features are added *after* setup: the roles assume the platform
    is installed and its containers are running.
    """
    from iblai_infra.terraform.state import load_state

    state = load_state(name)
    if state is None:
        ui.error(f"No environment found with name: {name}")
        ui.muted("  List them with [brand]iblai infra list[/brand]")
        raise typer.Exit(1)

    if state.status == "destroyed":
        ui.error(f"'{name}' has been destroyed.")
        raise typer.Exit(1)

    if state.setup_status != "completed":
        ui.error(f"'{name}' has not completed setup yet.")
        ui.muted(
            f"  Optional features are added after setup. Run "
            f"[brand]iblai infra setup {name}[/brand] first."
        )
        raise typer.Exit(1)

    return state


def confirm_restart(services: str, *, no_restart: bool, assume_yes: bool) -> bool:
    """Ask whether to recreate the services a change depends on.

    Restarting is what makes the change live, so it defaults to yes - but on a
    running environment it means a brief outage, which the operator should be
    told about rather than have happen silently.
    """
    import questionary

    if no_restart:
        return False
    if assume_yes:
        return True

    ui.newline()
    ui.muted(f"  {services} must be recreated before this takes effect.")
    ui.muted("  They will be briefly unavailable. edX takes the longest to come back.")
    answer = questionary.confirm(
        "Restart them once the configuration is written?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    return bool(answer)


def apply_feature(
    state: ProjectState,
    config: SetupConfig,
    tags: list[str],
    labels: dict[str, str],
    description: str,
) -> bool:
    """Run the tagged role(s) against the environment. Returns success."""
    from iblai_infra.ansible.runner import AnsibleRunner

    runner = AnsibleRunner(state, config, role_labels=labels)
    if not runner.preflight():
        raise typer.Exit(1)
    runner.setup()
    return runner.run_partial(tags, description=description)


def run_feature(
    state: ProjectState,
    config: SetupConfig,
    *,
    tags: list[str],
    labels: dict[str, str],
    name: str,
    what: str,
    action: str = "configured",
    live_now: bool = True,
    not_live_hint: str = "",
) -> None:
    """Apply a feature and report the outcome. Exits non-zero on failure.

    ``live_now`` is False when the operator declined a restart the change
    depends on - the run succeeded, but saying "configured" without qualifying
    it would be misleading.
    """
    ok = apply_feature(state, config, tags, labels, description=f"{what} {action}")

    ui.newline()
    if not ok:
        ui.error(f"Could not apply the {what} configuration.")
        raise typer.Exit(1)

    ui.success(f"{what} {action} on [highlight]{name}[/highlight].")
    if not live_now:
        ui.newline()
        ui.warning("The services were not restarted, so this is not live yet.")
        if not_live_hint:
            ui.muted(f"  {not_live_hint}")
    ui.newline()


def print_remote_config(
    state: ProjectState, keys: list[str], title: str, empty_hint: str
) -> None:
    """Read config values back off the server and print them.

    Nothing local records what an environment is configured with - the setup
    config carries secrets and is deliberately never persisted - so the server
    is the only source of truth.
    """
    from iblai_infra.ansible.runner import AnsibleRunner

    config = SetupConfig.for_feature(state)
    runner = AnsibleRunner(state, config)

    ui.newline()
    ui.console.rule(f"[brand]{title}[/brand]")

    values = runner.read_config_values(keys)
    if values is None:
        ui.error("Could not reach the server to read its configuration.")
        raise typer.Exit(1)

    if not any((v or "").strip() for v in values.values()):
        ui.muted(f"  {empty_hint}")
        ui.newline()
        return

    width = max(len(k) for k in keys)
    for key in keys:
        val = (values.get(key) or "").strip() or "[muted]not set[/muted]"
        ui.console.print(f"  {key:<{width}}  {val}")
    ui.newline()


def prompt_required(message: str, *, secret: bool = False, default: str | None = None) -> str:
    """Prompt for a value that must not be empty, aborting on Ctrl-C."""
    import questionary

    kwargs = dict(
        validate=lambda v: bool(v.strip()) or "Required",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    )
    if secret:
        answer = questionary.password(message, **kwargs).ask()
    else:
        if default is not None:
            kwargs["default"] = default
        answer = questionary.text(message, **kwargs).ask()

    if answer is None:
        ui.abort()
    return answer.strip()


def prompt_optional(message: str, default: str = "") -> str:
    """Prompt for a value that may be left blank."""
    import questionary

    answer = questionary.text(
        message, default=default, style=ui.PROMPT_STYLE, qmark=ui.QMARK
    ).ask()
    if answer is None:
        ui.abort()
    return answer.strip()
