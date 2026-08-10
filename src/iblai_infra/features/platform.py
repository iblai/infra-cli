"""``iblai infra platform`` — create an additional tenant platform.

A deployment always has the ``main`` platform. Creating another tenant is
often deferred until someone knows what it should be called, so this runs the
``ibl_tenant_platform`` role on its own against a bootstrapped environment.

The role walks the platform launcher (``run_launch_steps``) rather than
creating the record directly, so the tenant ends up fully wired - admin user,
role link, default apps, edX integration - instead of half-created. It checks
whether the platform already exists first, so re-running is safe.

It prints the new tenant's admin credentials once, at the end. They are not
persisted anywhere, so that output is the only copy.
"""

from __future__ import annotations

import re

import typer

from iblai_infra import ui
from iblai_infra.features._common import load_feature_target, prompt_required, run_feature
from iblai_infra.models import SetupConfig

platform_app = typer.Typer(
    name="platform",
    help="Create an additional tenant platform on an existing environment",
    no_args_is_help=True,
)

PLATFORM_TAGS = ["platform"]
PLATFORM_LABELS = {"ibl_tenant_platform": "Tenant Platform"}

# The platform name becomes a key in config and a slug in URLs.
VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@platform_app.command("create")
def platform_create(
    name: str = typer.Argument(help="Environment name"),
    platform_name: str = typer.Option(
        None, "--platform-name", help="Name of the tenant to create (skips the prompt)"
    ),
) -> None:
    """Create a tenant platform alongside the default `main` one."""
    state = load_feature_target(name)

    if platform_name is None:
        ui.newline()
        ui.console.rule("[brand]Tenant platform[/brand]")
        ui.muted(f"  Creating an additional tenant on [highlight]{name}[/highlight]")
        ui.muted("  Lowercase letters, numbers, hyphens and underscores.")
        ui.newline()
        platform_name = prompt_required("Platform name:")

    platform_name = platform_name.strip().lower()

    if platform_name == "main":
        ui.error("'main' is the default platform and already exists.")
        raise typer.Exit(1)

    if not VALID_NAME.match(platform_name):
        ui.error(
            f"'{platform_name}' is not a valid platform name — use lowercase "
            "letters, numbers, hyphens and underscores, starting with a letter or digit."
        )
        raise typer.Exit(1)

    config = SetupConfig.for_feature(state, platform_name=platform_name)

    run_feature(
        state, config, tags=PLATFORM_TAGS, labels=PLATFORM_LABELS,
        name=name, what=f"Tenant platform '{platform_name}'", action="created",
    )
    ui.warning("The tenant admin credentials are printed above and stored nowhere else.")
    ui.muted("  Save them now if the platform was newly created.")
    ui.newline()
