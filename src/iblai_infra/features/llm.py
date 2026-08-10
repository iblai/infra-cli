"""``iblai infra llm`` — set the LLM API key on a running environment.

The smallest of the optional integrations: one idempotent database row
(``GlobalCredential`` named ``openai``, marked preferred), written by a single
tagged task. The mentor service reads it per request, so a new key takes
effect immediately and nothing restarts.

Rotating a key is the same operation as setting one for the first time, which
is why there is a single `set-key` command rather than enable/disable.
"""

from __future__ import annotations

import typer

from iblai_infra import ui
from iblai_infra.features._common import load_feature_target, prompt_required, run_feature
from iblai_infra.models import SetupConfig

llm_app = typer.Typer(
    name="llm",
    help="Set the LLM API key on an existing environment",
    no_args_is_help=True,
)

LLM_TAGS = ["llm"]
LLM_LABELS = {"admin_setup": "LLM Credential"}


@llm_app.command("set-key")
def llm_set_key(
    name: str = typer.Argument(help="Environment name"),
    api_key: str = typer.Option(
        None, "--api-key", help="Read the key from a flag instead of prompting (CI)"
    ),
) -> None:
    """Set or rotate the LLM API key used by the mentor service."""
    state = load_feature_target(name)

    if api_key is None:
        ui.newline()
        ui.console.rule("[brand]LLM API key[/brand]")
        ui.muted(f"  Setting the mentor service credential for [highlight]{name}[/highlight]")
        ui.newline()
        api_key = prompt_required("API key:", secret=True)
    else:
        api_key = api_key.strip()

    if not api_key:
        ui.error("The API key is empty.")
        raise typer.Exit(1)

    config = SetupConfig.for_feature(state, openai_api_key=api_key)

    run_feature(
        state, config, tags=LLM_TAGS, labels=LLM_LABELS,
        name=name, what="LLM API key", action="set",
    )
    ui.muted("  The mentor service reads it per request — no restart needed.")
    ui.newline()
