"""Tests for iblai_infra.ui — branded output helpers and table builders."""

from __future__ import annotations

from io import StringIO
from unittest import mock

import pytest
from rich.console import Console
from rich.table import Table

from iblai_infra import ui


@pytest.fixture
def capture_console():
    """Create a console that captures output to a string buffer, with IBL theme."""
    buf = StringIO()
    console = Console(file=buf, width=80, force_terminal=True, theme=ui.IBL_THEME)
    with mock.patch.object(ui, "console", console):
        yield buf


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


class TestOutputHelpers:
    def test_success_prints_checkmark(self, capture_console):
        ui.success("All good")
        output = capture_console.getvalue()
        assert "\u2713" in output
        assert "All good" in output

    def test_warning_prints_warning_sign(self, capture_console):
        ui.warning("Careful")
        output = capture_console.getvalue()
        assert "\u26a0" in output
        assert "Careful" in output

    def test_error_prints_cross(self, capture_console):
        ui.error("Something broke")
        output = capture_console.getvalue()
        assert "\u2717" in output
        assert "Something broke" in output

    def test_info_prints_bullet(self, capture_console):
        ui.info("For your information")
        output = capture_console.getvalue()
        assert "\u25cf" in output
        assert "For your information" in output

    def test_muted_prints_dimmed(self, capture_console):
        ui.muted("Quiet message")
        output = capture_console.getvalue()
        assert "Quiet message" in output

    def test_newline_prints_empty(self, capture_console):
        ui.newline()
        output = capture_console.getvalue()
        assert output.strip() == ""


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


class TestBanner:
    def test_banner_wide_terminal(self):
        buf = StringIO()
        console = Console(file=buf, width=80, force_terminal=True, theme=ui.IBL_THEME)
        with mock.patch.object(ui, "console", console):
            ui.banner()
        output = buf.getvalue()
        # Wide terminal should show ASCII art
        assert "ibl" in output.lower() or "_" in output
        assert "Infrastructure Provisioning" in output

    def test_banner_narrow_terminal(self):
        buf = StringIO()
        console = Console(file=buf, width=30, force_terminal=True, theme=ui.IBL_THEME)
        with mock.patch.object(ui, "console", console):
            ui.banner()
        output = buf.getvalue()
        assert "Infrastructure Provisioning" in output


# ---------------------------------------------------------------------------
# step_header
# ---------------------------------------------------------------------------


class TestStepHeader:
    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def test_step_header_content(self, capture_console):
        ui.step_header(2, 5, "Configure Network")
        output = self._strip_ansi(capture_console.getvalue())
        assert "Step 2 of 5" in output
        assert "Configure Network" in output

    def test_step_header_first_step(self, capture_console):
        ui.step_header(1, 3, "First Step")
        output = self._strip_ansi(capture_console.getvalue())
        assert "Step 1 of 3" in output

    def test_step_header_last_step(self, capture_console):
        ui.step_header(3, 3, "Last Step")
        output = self._strip_ansi(capture_console.getvalue())
        assert "Step 3 of 3" in output


# ---------------------------------------------------------------------------
# abort
# ---------------------------------------------------------------------------


class TestAbort:
    def test_abort_raises_system_exit(self, capture_console):
        with pytest.raises(SystemExit) as exc_info:
            ui.abort()
        assert exc_info.value.code == 1

    def test_abort_custom_message(self, capture_console):
        with pytest.raises(SystemExit):
            ui.abort("Custom abort message")
        output = capture_console.getvalue()
        assert "Custom abort message" in output

    def test_abort_default_message(self, capture_console):
        with pytest.raises(SystemExit):
            ui.abort()
        output = capture_console.getvalue()
        assert "Aborted" in output


# ---------------------------------------------------------------------------
# build_resource_table
# ---------------------------------------------------------------------------


class TestBuildResourceTable:
    def test_empty_resources(self):
        table = ui.build_resource_table({})
        assert isinstance(table, Table)

    def test_creating_labels_by_default(self):
        resources = {
            "aws_vpc.main": {
                "label": "VPC (main)",
                "status": "complete",
                "elapsed": 5,
            }
        }
        table = ui.build_resource_table(resources)
        assert isinstance(table, Table)

    def test_destroying_labels(self):
        resources = {
            "aws_vpc.main": {
                "label": "VPC (main)",
                "status": "complete",
                "elapsed": 5,
            }
        }
        # Render to string to check labels
        buf = StringIO()
        console = Console(file=buf, width=100, force_terminal=True, theme=ui.IBL_THEME)
        table = ui.build_resource_table(resources, destroying=True)
        console.print(table)
        output = buf.getvalue()
        assert "Destroyed" in output

    def test_create_labels(self):
        resources = {
            "aws_vpc.main": {
                "label": "VPC (main)",
                "status": "complete",
                "elapsed": 5,
            }
        }
        buf = StringIO()
        console = Console(file=buf, width=100, force_terminal=True, theme=ui.IBL_THEME)
        table = ui.build_resource_table(resources, destroying=False)
        console.print(table)
        output = buf.getvalue()
        assert "Created" in output

    def test_all_status_types(self):
        resources = {
            "a": {"label": "A", "status": "complete", "elapsed": 3},
            "b": {"label": "B", "status": "in_progress", "elapsed": 1},
            "c": {"label": "C", "status": "error", "elapsed": 2},
            "d": {"label": "D", "status": "pending", "elapsed": 0},
        }
        buf = StringIO()
        console = Console(file=buf, width=100, force_terminal=True, theme=ui.IBL_THEME)
        table = ui.build_resource_table(resources)
        console.print(table)
        output = buf.getvalue()
        assert "Failed" in output
        assert "Pending" in output
        assert "Creating" in output

    def test_elapsed_zero_shows_dash(self):
        resources = {
            "a": {"label": "A", "status": "pending", "elapsed": 0},
        }
        buf = StringIO()
        console = Console(file=buf, width=100, force_terminal=True, theme=ui.IBL_THEME)
        table = ui.build_resource_table(resources)
        console.print(table)
        output = buf.getvalue()
        assert "\u2014" in output


# ---------------------------------------------------------------------------
# section and section_group
# ---------------------------------------------------------------------------


class TestSection:
    def test_section_renders(self, capture_console):
        ui.section("Test Title", "Some content")
        output = capture_console.getvalue()
        assert "Test Title" in output
        assert "Some content" in output

    def test_section_group_returns_group(self):
        from rich.console import Group

        group = ui.section_group("Title", "Content")
        assert isinstance(group, Group)


# ---------------------------------------------------------------------------
# summary_panel
# ---------------------------------------------------------------------------


class TestSummaryPanel:
    def test_summary_panel_renders(self, capture_console):
        rows = [
            ("Name", "testproject"),
            ("Region", "us-east-1"),
        ]
        ui.summary_panel("Summary", rows)
        output = capture_console.getvalue()
        assert "Summary" in output
        assert "testproject" in output
        assert "us-east-1" in output

    def test_summary_panel_bold_section_header(self, capture_console):
        rows = [
            ("", "[bold]Section Header[/bold]"),
            ("Key", "value"),
        ]
        ui.summary_panel("Title", rows)
        output = capture_console.getvalue()
        assert "Title" in output


# ---------------------------------------------------------------------------
# make_overall_progress
# ---------------------------------------------------------------------------


class TestMakeOverallProgress:
    def test_returns_progress(self):
        progress = ui.make_overall_progress()
        from rich.progress import Progress

        assert isinstance(progress, Progress)
