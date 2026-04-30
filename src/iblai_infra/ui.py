"""Rich console wrapper — ibl.ai branded output, themes, and helpers.

Brand color: #2175C5 (ibl.ai primary blue)
Palette derived from the brand blue for terminal use.
"""

from __future__ import annotations

import questionary

from rich.console import Console, Group, RenderableType
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.theme import Theme

# ---------------------------------------------------------------------------
# ibl.ai Brand Palette
# ---------------------------------------------------------------------------
# Primary:  #2175C5  (ibl.ai blue)
# Light:    #5BA3E0  (hover / active highlights)
# Pale:     #A8D0F2  (subtle accents)
# Dark:     #174E87  (deep blue for emphasis)
# Navy:     #0E3259  (darkest shade)

IBL_BLUE = "#2175C5"
IBL_BLUE_LIGHT = "#5BA3E0"
IBL_BLUE_PALE = "#A8D0F2"
IBL_BLUE_DARK = "#174E87"
IBL_NAVY = "#0E3259"

# ---------------------------------------------------------------------------
# Rich Theme
# ---------------------------------------------------------------------------

IBL_THEME = Theme(
    {
        "brand": f"bold {IBL_BLUE}",
        "brand.light": f"{IBL_BLUE_LIGHT}",
        "brand.dark": f"bold {IBL_BLUE_DARK}",
        "step": f"bold {IBL_BLUE_LIGHT}",
        "success": "bold #3ECF6E",
        "warning": "bold #F0A830",
        "error": "bold #E85454",
        "info": f"bold {IBL_BLUE}",
        "muted": "dim",
        "highlight": "bold white",
        "key": f"{IBL_BLUE_LIGHT}",
        "value": "white",
        "section": f"bold {IBL_BLUE_PALE}",
    }
)

console = Console(theme=IBL_THEME)

# ---------------------------------------------------------------------------
# Questionary Theme (consistent ibl.ai branding)
# ---------------------------------------------------------------------------

PROMPT_STYLE = questionary.Style(
    [
        ("qmark", f"fg:{IBL_BLUE} bold"),
        ("question", "fg:white bold"),
        ("answer", "fg:#3ECF6E bold"),
        ("pointer", f"fg:{IBL_BLUE_LIGHT} bold"),
        ("highlighted", f"fg:{IBL_BLUE_LIGHT} bold"),
        ("selected", "fg:#3ECF6E"),
        ("separator", "fg:#4A5568"),
        ("instruction", "fg:#718096"),
        ("text", "fg:white"),
    ]
)

QMARK = "  ?"

# ---------------------------------------------------------------------------
# Branded output helpers
# ---------------------------------------------------------------------------


def banner() -> None:
    """Print the ibl.ai welcome banner."""
    console.print()
    console.print(Rule(style=IBL_BLUE))

    if console.width >= 40:
        console.print(
            f"[bold {IBL_BLUE}]"
            " _ _     _           _\n"
            "(_) |__ | |    __ _ (_)\n"
            "| | '_ \\| |   / _` || |\n"
            "| | |_) | |_ | (_| || |\n"
            "|_|_.__/|____(_)__,_|_|"
            "[/]\n"
            "\n"
            "[bold white]Infrastructure Provisioning[/bold white]",
            justify="center",
        )
    else:
        console.print(
            f"\n[brand]ibl.ai[/brand]\n"
            "[bold white]Infrastructure Provisioning[/bold white]",
            justify="center",
        )

    console.print()


def step_header(step: int, total: int, title: str) -> None:
    """Print a step progress header with visual breadcrumb."""
    console.print()
    filled = step - 1
    remaining = total - step
    bar = f"[{IBL_BLUE}]" + "\u2501" * (filled * 2) + f"[/]"
    if remaining > 0:
        bar += "[muted]" + "\u2501" * (remaining * 2) + "[/muted]"
    console.print(
        f"  {bar}  [step]Step {step} of {total}[/step]"
        f" [muted]\u2014[/muted] [highlight]{title}[/highlight]"
    )
    console.print()


def success(message: str) -> None:
    console.print(f"  [success]\u2713[/success] {message}")


def warning(message: str) -> None:
    console.print(f"  [warning]\u26a0[/warning]  {message}")


def error(message: str) -> None:
    console.print(f"  [error]\u2717[/error] {message}")


def info(message: str) -> None:
    console.print(f"  [info]\u25cf[/info] {message}")


def muted(message: str) -> None:
    console.print(f"  [muted]{message}[/muted]")


def newline() -> None:
    console.print()


def section(title: str, content: RenderableType) -> None:
    """Print content between two horizontal rules with a centered title."""
    console.print()
    console.print(Rule(f"[brand]{title}[/brand]", style=IBL_BLUE))
    console.print(content)
    console.print(Rule(style=IBL_BLUE))
    console.print()


def section_group(title: str, content: RenderableType) -> Group:
    """Return a Group of rule + content + rule for use in Live displays."""
    return Group(
        Rule(f"[brand]{title}[/brand]", style=IBL_BLUE),
        content,
        Rule(style=IBL_BLUE),
    )


def summary_panel(title: str, rows: list[tuple[str, str]]) -> None:
    """Print a summary with key-value rows between horizontal rules."""
    table = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    table.add_column("Key", style="key", min_width=18)
    table.add_column("Value", style="value")

    for key, value in rows:
        # Render section headers in brand blue
        if value and value.startswith("[bold]"):
            value = value.replace("[bold]", f"[bold {IBL_BLUE_PALE}]").replace("[/bold]", "[/]")
        table.add_row(key, value)

    section(title, table)


def abort(message: str = "Aborted.") -> None:
    """Print abort message and exit."""
    console.print()
    error(message)
    console.print()
    raise SystemExit(1)


# Notice shown before any ansible-driven setup or launch. The platform's
# ansible roles install two private Python packages (`iblai-cli-ops` and
# `iblai-prod-images`) and `docker pull` every container image from a
# private ECR registry. Without access, the run fails part-way through
# with auth errors that are hard to map back to "you don't have
# permission yet" — usually buried inside `pip install` or `docker
# login` output 5-30 minutes deep.
#
# We deliberately do NOT name our GitHub org or our ECR account ID in
# user-facing text — both are operational details delivered as part of
# the access-grant during onboarding (along with the GitHub PAT + AWS
# IAM credentials that the operator pastes into the existing prompts).
# Surfacing this requirement at the top of every setup / launch flow
# lets a first-time operator (or someone trying the open-source repo)
# confirm they're set up correctly OR bail before spending time and
# AWS dollars on a doomed run.
_PRIVATE_ACCESS_NOTICE = """\
This setup needs access to three private IBL resources:

  • iblai-cli-ops
  • iblai-prod-images
  • IBL's private ECR registry

You'll be asked for credentials in the next steps. If something fails
or you don't have access yet, reach out at https://ibl.ai/contact/
"""


def private_access_notice() -> None:
    """Print the IBL private-access prerequisites notice.

    Static, non-technical message naming the three private resources
    operators need access to. Always shown right before the
    ansible-driven phase of `iblai infra setup` / `iblai infra launch`
    / `iblai infra launch-env` begins.

    Interactive flows follow this with a `questionary.confirm` so the
    operator can bail; non-interactive flows print it as a heads-up so
    a CI run that fails has an obvious explanation in its stdout.
    """
    section(
        "Prerequisites — IBL platform access required",
        _PRIVATE_ACCESS_NOTICE,
    )


# ---------------------------------------------------------------------------
# Progress display builders
# ---------------------------------------------------------------------------


def make_overall_progress() -> Progress:
    """Create the main progress bar styled with ibl.ai blue."""
    return Progress(
        SpinnerColumn("dots", style=IBL_BLUE_LIGHT),
        TextColumn(f"[bold {IBL_BLUE}]{{task.description}}[/]"),
        BarColumn(
            bar_width=30,
            style=IBL_NAVY,
            complete_style=IBL_BLUE,
            finished_style="#3ECF6E",
        ),
        TextColumn("[bold white]{task.percentage:>3.0f}%[/]"),
        TextColumn(f"[{IBL_BLUE_PALE}]({{task.completed}}/{{task.total}})[/]"),
        TimeElapsedColumn(),
        console=console,
    )


def build_resource_table(resources: dict[str, dict], destroying: bool = False) -> Table:
    """Build a live-updating resource status table."""
    table = Table(
        show_header=True,
        header_style=f"bold {IBL_BLUE_LIGHT}",
        border_style=IBL_NAVY,
        padding=(0, 1),
        expand=False,
        min_width=60,
    )
    table.add_column("Resource", style="white", min_width=35)
    table.add_column("Status", min_width=14, justify="center")
    table.add_column("Time", justify="right", min_width=6, style=IBL_BLUE_PALE)

    done_label = "Destroyed" if destroying else "Created"
    active_label = "Destroying" if destroying else "Creating"

    for addr, info in resources.items():
        status = info.get("status", "pending")
        elapsed = info.get("elapsed", 0)
        friendly = info.get("label", addr)

        if status == "complete":
            status_display = f"[bold #3ECF6E]\u2713 {done_label}[/]"
        elif status == "in_progress":
            status_display = f"[bold {IBL_BLUE_LIGHT}]\u25cf {active_label}[/]"
        elif status == "error":
            status_display = "[bold #E85454]\u2717 Failed[/]"
        else:
            status_display = "[dim]\u25cb Pending[/dim]"

        time_display = f"{elapsed}s" if elapsed else "\u2014"
        table.add_row(friendly, status_display, time_display)

    return table
