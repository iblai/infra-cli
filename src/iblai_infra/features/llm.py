"""``iblai infra llm`` — set the LLM API key on a running environment.

The smallest of the optional integrations: one idempotent database row
(a credential named for its provider, marked preferred), written by a single
tagged task. The mentor service reads it per request, so a new key takes
effect immediately and nothing restarts.

Rotating a key is the same operation as setting one for the first time, which
is why there is a single `set-key` command rather than enable/disable.

Setting a key also makes its provider the preferred one, since the server picks
the preferred credential without any tie-break when several are marked.
"""

from __future__ import annotations

import typer
from pydantic import ValidationError

from iblai_infra import ui
from iblai_infra.features._common import load_feature_target, prompt_required, run_feature
from iblai_infra.models import LLMProvider, SetupConfig

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
    provider: str = typer.Option(
        None, "--provider", help="Which provider the key belongs to (openai or anthropic)"
    ),
) -> None:
    """Set or rotate the LLM API key used by the mentor service."""
    import questionary

    state = load_feature_target(name)
    interactive = api_key is None

    if interactive:
        ui.newline()
        ui.console.rule("[brand]LLM API key[/brand]")
        ui.muted(f"  Setting the mentor service credential for [highlight]{name}[/highlight]")
        ui.newline()

    if provider is None:
        if interactive:
            chosen = questionary.select(
                "Which provider is this key for?",
                choices=[p.value for p in LLMProvider],
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if chosen is None:
                ui.abort()
            provider = chosen
        else:
            provider = LLMProvider.OPENAI.value

    # The value becomes the credential's name and the server matches it
    # exactly, so an unrecognised or differently-cased provider would write a
    # row nothing ever reads.
    try:
        llm_provider = LLMProvider(provider.strip().lower())
    except ValueError:
        ui.error(f"'{provider}' is not a supported provider.")
        ui.muted(f"  Choose one of: {', '.join(p.value for p in LLMProvider)}")
        raise typer.Exit(1)

    api_key = prompt_required("API key:", secret=True) if interactive else api_key.strip()

    if not api_key:
        ui.error("The API key is empty.")
        raise typer.Exit(1)

    # SetupConfig rejects a key that could not have come from a provider - the
    # value ends up inside a shell command. Caught here so an operator gets a
    # sentence rather than a validation traceback.
    try:
        config = SetupConfig.for_feature(
            state, llm_provider=llm_provider, llm_api_key=api_key
        )
    except ValidationError:
        ui.error("That does not look like an API key.")
        ui.muted("  Expected letters, numbers, dashes, underscores and dots only.")
        raise typer.Exit(1)

    run_feature(
        state, config, tags=LLM_TAGS, labels=LLM_LABELS,
        name=name, what=f"{llm_provider.value} API key", action="set",
    )
    ui.muted("  The mentor service reads it per request — no restart needed.")
    ui.muted(f"  [highlight]{llm_provider.value}[/highlight] is now the preferred provider.")
    ui.newline()
