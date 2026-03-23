"""Tests for iblai_infra.cli — command routing, state checks, edge cases."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import click
import pytest
import typer
from typer.testing import CliRunner

from iblai_infra import __version__
from iblai_infra.cli import app, _run_setup_provisioned, _interactive_setup, _resolve_credentials
from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DNSConfig,
    Environment,
    InfraConfig,
    NetworkConfig,
    ProjectState,
    SSHConfig,
    SSHKeyMethod,
)

runner = CliRunner()

# typer.Exit wraps click.exceptions.Exit which may or may not inherit SystemExit
_EXIT_EXCEPTIONS = (SystemExit, typer.Exit, click.exceptions.Exit)


# ---------------------------------------------------------------------------
# Version command
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout

    def test_version_short_flag(self):
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0
        assert __version__ in result.stdout


# ---------------------------------------------------------------------------
# No args shows help
# ---------------------------------------------------------------------------


class TestNoArgs:
    def test_root_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # no_args_is_help may return exit code 0 or 2 depending on Typer version
        assert result.exit_code in (0, 2)
        assert "infra" in result.stdout or "help" in result.stdout.lower()


# ---------------------------------------------------------------------------
# _run_setup — all state validation branches
# ---------------------------------------------------------------------------


class TestRunSetup:
    def test_not_found(self):
        with patch("iblai_infra.cli.load_state", return_value=None):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("nonexistent")

    def test_destroyed_state(self, project_state):
        project_state.status = "destroyed"
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_initialized_state(self, project_state):
        project_state.status = "initialized"
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_failed_state(self, project_state):
        project_state.status = "failed"
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_no_outputs(self, project_state):
        project_state.outputs = None
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_no_instance_ip(self, project_state):
        project_state.outputs = {"alb_dns_name": "some-alb.amazonaws.com"}
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_empty_instance_ip(self, project_state):
        project_state.outputs = {"instance_public_ip": ""}
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_already_completed_decline_rerun(self, project_state):
        project_state.setup_status = "completed"
        with (
            patch("iblai_infra.cli.load_state", return_value=project_state),
            patch("questionary.confirm") as mock_confirm,
        ):
            mock_confirm.return_value.ask.return_value = False
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_already_completed_accept_rerun(self, project_state):
        """User accepts re-running setup, but ansible not installed."""
        project_state.setup_status = "completed"
        with (
            patch("iblai_infra.cli.load_state", return_value=project_state),
            patch("questionary.confirm") as mock_confirm,
            patch("shutil.which", return_value=None),
        ):
            mock_confirm.return_value.ask.return_value = True
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")

    def test_ansible_not_installed(self, project_state):
        with (
            patch("iblai_infra.cli.load_state", return_value=project_state),
            patch("shutil.which", return_value=None),
        ):
            with pytest.raises(_EXIT_EXCEPTIONS):
                _run_setup_provisioned("testproject")


# ---------------------------------------------------------------------------
# _interactive_setup — selection logic
# ---------------------------------------------------------------------------


class TestInteractiveSetup:
    def test_no_eligible_goes_to_existing_server(self):
        """With no provisioned environments, goes directly to existing server flow."""
        with (
            patch("iblai_infra.cli.list_all_states", return_value=[]),
            patch("iblai_infra.cli._run_setup_interactive") as mock_run,
        ):
            _interactive_setup()
            mock_run.assert_called_once()

    def test_no_created_goes_to_existing_server(self, project_state):
        project_state.status = "initialized"
        with (
            patch("iblai_infra.cli.list_all_states", return_value=[project_state]),
            patch("iblai_infra.cli._run_setup_interactive") as mock_run,
        ):
            _interactive_setup()
            mock_run.assert_called_once()

    def test_eligible_env_choose_provisioned(self, project_state):
        """User selects 'provisioned' path, then single env goes directly to setup."""
        with (
            patch("iblai_infra.cli.list_all_states", return_value=[project_state]),
            patch("questionary.select") as mock_select,
            patch("iblai_infra.cli._run_setup_provisioned") as mock_run,
        ):
            mock_select.return_value.ask.return_value = "provisioned"
            _interactive_setup()
            mock_run.assert_called_once_with("testproject")

    def test_eligible_env_choose_existing(self, project_state):
        """User selects 'existing' path even though provisioned envs exist."""
        with (
            patch("iblai_infra.cli.list_all_states", return_value=[project_state]),
            patch("questionary.select") as mock_select,
            patch("iblai_infra.cli._run_setup_interactive") as mock_run,
        ):
            mock_select.return_value.ask.return_value = "existing"
            _interactive_setup()
            mock_run.assert_called_once()

    def test_multiple_eligible_prompts_env_selection(self, project_state):
        state2 = project_state.model_copy()
        state2.name = "project2"
        with (
            patch("iblai_infra.cli.list_all_states", return_value=[project_state, state2]),
            patch("questionary.select") as mock_select,
            patch("iblai_infra.cli._run_setup_provisioned") as mock_run,
        ):
            # First select: path choice, second select: env choice
            mock_select.return_value.ask.side_effect = ["provisioned", "project2"]
            _interactive_setup()
            mock_run.assert_called_once_with("project2")

    def test_user_cancels_path_selection(self, project_state):
        with (
            patch("iblai_infra.cli.list_all_states", return_value=[project_state]),
            patch("questionary.select") as mock_select,
        ):
            mock_select.return_value.ask.return_value = None
            _interactive_setup()  # Should just return


# ---------------------------------------------------------------------------
# destroy command — state checks
# ---------------------------------------------------------------------------


class TestDestroyCommand:
    def test_destroy_not_found(self):
        result = runner.invoke(app, ["infra", "destroy", "nonexistent"])
        assert result.exit_code != 0

    def test_destroy_already_destroyed(self, project_state):
        project_state.status = "destroyed"
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            result = runner.invoke(app, ["infra", "destroy", "testproject"])
            assert result.exit_code == 0

    def test_destroy_user_declines(self, project_state):
        with (
            patch("iblai_infra.cli.load_state", return_value=project_state),
            patch("questionary.confirm") as mock_confirm,
        ):
            mock_confirm.return_value.ask.return_value = False
            with pytest.raises(SystemExit):
                from iblai_infra.cli import destroy
                destroy("testproject")

    def test_destroy_prod_name_mismatch(self, project_state):
        project_state.config.environment = Environment.PROD
        # Need to re-validate since we're modifying an existing object
        with (
            patch("iblai_infra.cli.load_state", return_value=project_state),
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
        ):
            mock_confirm.return_value.ask.return_value = True
            mock_text.return_value.ask.return_value = "wrong-name"
            with pytest.raises(SystemExit):
                from iblai_infra.cli import destroy
                destroy("testproject")


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_not_found(self):
        with patch("iblai_infra.cli.load_state", return_value=None):
            result = runner.invoke(app, ["infra", "status", "nonexistent"])
            assert result.exit_code != 0

    def test_status_created(self, project_state):
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            result = runner.invoke(app, ["infra", "status", "testproject"])
            assert result.exit_code == 0
            assert "testproject" in result.stdout

    def test_status_with_setup_completed(self, project_state):
        project_state.setup_status = "completed"
        project_state.setup_completed_at = datetime(2025, 1, 20, tzinfo=timezone.utc)
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            result = runner.invoke(app, ["infra", "status", "testproject"])
            assert result.exit_code == 0

    def test_status_no_outputs(self, project_state):
        project_state.outputs = None
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            result = runner.invoke(app, ["infra", "status", "testproject"])
            assert result.exit_code == 0

    def test_status_workspace_missing(self, project_state, tmp_path):
        project_state.workspace_path = str(tmp_path / "nonexistent")
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            result = runner.invoke(app, ["infra", "status", "testproject"])
            assert result.exit_code == 0

    def test_status_all_status_colors(self, project_state):
        for status in ("initialized", "created", "failed", "destroyed"):
            project_state.status = status
            with patch("iblai_infra.cli.load_state", return_value=project_state):
                result = runner.invoke(app, ["infra", "status", "testproject"])
                assert result.exit_code == 0

    def test_status_with_ssh_key(self, project_state):
        project_state.config.ssh.private_key_path = Path("/home/user/.ssh/mykey.pem")
        with patch("iblai_infra.cli.load_state", return_value=project_state):
            result = runner.invoke(app, ["infra", "status", "testproject"])
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_list_empty(self):
        with patch("iblai_infra.cli.list_all_states", return_value=[]):
            result = runner.invoke(app, ["infra", "list"])
            assert result.exit_code == 0
            assert "No managed infrastructure" in result.stdout

    def test_list_filters_destroyed(self, project_state):
        destroyed = project_state.model_copy()
        destroyed.name = "old-project"
        destroyed.status = "destroyed"
        with patch("iblai_infra.cli.list_all_states", return_value=[project_state, destroyed]):
            result = runner.invoke(app, ["infra", "list"])
            assert result.exit_code == 0
            assert "testproject" in result.stdout
            # Destroyed should be filtered
            assert "old-project" not in result.stdout

    def test_list_multiple_states(self, project_state):
        state2 = project_state.model_copy()
        state2.name = "second-project"
        state2.config = project_state.config.model_copy()
        state2.status = "initialized"
        with patch("iblai_infra.cli.list_all_states", return_value=[project_state, state2]):
            result = runner.invoke(app, ["infra", "list"])
            assert result.exit_code == 0
            assert "2 environment(s)" in result.stdout

    def test_list_setup_status_display(self, project_state):
        project_state.setup_status = "completed"
        with patch("iblai_infra.cli.list_all_states", return_value=[project_state]):
            result = runner.invoke(app, ["infra", "list"])
            assert result.exit_code == 0
            # Table is rendered — "testproject" should be in output
            assert "testproject" in result.stdout

    def test_list_no_setup_status(self, project_state):
        project_state.setup_status = None
        with patch("iblai_infra.cli.list_all_states", return_value=[project_state]):
            result = runner.invoke(app, ["infra", "list"])
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# _resolve_credentials — all paths
# ---------------------------------------------------------------------------


class TestResolveCredentials:
    def test_explicit_profile_success(self):
        identity = MagicMock()
        identity.account_id = "123456789012"
        identity.arn = "arn:aws:iam::123456789012:user/admin"

        with (
            patch("iblai_infra.providers.aws.validate_credentials", return_value=identity),
            patch("iblai_infra.cli.save_session"),
        ):
            result_creds, result_id = _resolve_credentials(profile="myprofile", region="us-east-1")
            assert result_creds.profile == "myprofile"

    def test_explicit_profile_failure_falls_through(self):
        with (
            patch("iblai_infra.providers.aws.validate_credentials", side_effect=ValueError("bad")),
            patch("iblai_infra.cli.load_session", return_value=None),
            patch("iblai_infra.prompts.credentials.prompt_credentials") as mock_prompt,
        ):
            mock_creds = AWSCredentials(
                method=AuthMethod.ACCESS_KEY,
                access_key_id="AK",
                secret_access_key="SK",
                region="us-east-1",
                account_id="111",
                arn="arn:aws:iam::111:user/test",
            )
            mock_prompt.return_value = mock_creds
            _resolve_credentials(profile="badprofile")

    def test_saved_session_reused(self):
        creds = AWSCredentials(
            method=AuthMethod.PROFILE,
            profile="saved",
            region="us-east-1",
            account_id="123",
            arn="arn:aws:iam::123:user/saved",
        )
        identity = MagicMock()
        identity.account_id = "123"
        identity.arn = "arn:aws:iam::123:user/saved"

        with patch("iblai_infra.cli.load_session", return_value=(creds, identity)):
            result_creds, _ = _resolve_credentials()
            assert result_creds.profile == "saved"

    def test_no_session_prompts_credentials(self):
        mock_creds = AWSCredentials(
            method=AuthMethod.ENVIRONMENT,
            region="us-east-1",
            account_id="999",
            arn="arn:aws:iam::999:user/env",
        )
        with (
            patch("iblai_infra.cli.load_session", return_value=None),
            patch("iblai_infra.prompts.credentials.prompt_credentials", return_value=mock_creds) as mock_prompt,
        ):
            _resolve_credentials()
            mock_prompt.assert_called_once()


# ---------------------------------------------------------------------------
# permissions command
# ---------------------------------------------------------------------------


class TestPermissionsCommand:
    def test_show_policy_no_check(self):
        result = runner.invoke(app, ["infra", "permissions"])
        assert result.exit_code == 0
        assert "ec2:*" in result.stdout
        assert "s3:*" in result.stdout
