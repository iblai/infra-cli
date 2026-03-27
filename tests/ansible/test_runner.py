"""Tests for iblai_infra.ansible.runner — line parsing, extra vars, pre-flight."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iblai_infra.ansible.runner import LAUNCH_ROLE_LABELS, ROLE_LABELS, TOTAL_ROLES, AnsibleRunner


# ---------------------------------------------------------------------------
# Line-based role extraction (default callback output)
# ---------------------------------------------------------------------------


class TestExtractRoleFromLine:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        r.ws = Path("/tmp/ansible-test")
        r.playbook = "playbook.yml"
        r.role_labels = ROLE_LABELS
        return r

    def test_task_with_role(self, runner):
        line = "TASK [docker : Install Docker CE] ************************************"
        assert runner._extract_role_from_line(line) == "docker"

    def test_task_with_awscli_role(self, runner):
        line = "TASK [awscli : Install AWS CLI v2] ***"
        assert runner._extract_role_from_line(line) == "awscli"

    def test_task_with_python_role(self, runner):
        line = "TASK [python : Install pyenv] ***"
        assert runner._extract_role_from_line(line) == "python"

    def test_task_with_ibl_cli_ops_role(self, runner):
        line = "TASK [ibl_cli_ops : Clone ibl-cli-ops repo] ***"
        assert runner._extract_role_from_line(line) == "ibl_cli_ops"

    def test_task_with_ibl_platform_role(self, runner):
        line = "TASK [ibl_platform : Configure edX version] ***"
        assert runner._extract_role_from_line(line) == "ibl_platform"

    def test_task_with_ibl_dm_role(self, runner):
        line = "TASK [ibl_dm : Launch IBL Manager] ***"
        assert runner._extract_role_from_line(line) == "ibl_dm"

    def test_task_with_ibl_edx_role(self, runner):
        line = "TASK [ibl_edx : Launch Open edX] ***"
        assert runner._extract_role_from_line(line) == "ibl_edx"

    def test_task_without_role(self, runner):
        line = "TASK [Wait for cloud-init to finish] ***"
        assert runner._extract_role_from_line(line) is None

    def test_task_unknown_role(self, runner):
        line = "TASK [some_other_role : Do something] ***"
        assert runner._extract_role_from_line(line) is None

    def test_not_a_task_line(self, runner):
        assert runner._extract_role_from_line("PLAY [Bootstrap IBL Platform] ***") is None
        assert runner._extract_role_from_line("ok: [32.192.6.92]") is None
        assert runner._extract_role_from_line("changed: [32.192.6.92]") is None
        assert runner._extract_role_from_line("") is None

    def test_play_recap(self, runner):
        assert runner._extract_role_from_line("PLAY RECAP ***") is None

    def test_task_name_contains_role_keyword(self, runner):
        line = "TASK [Gathering Facts] ***"
        assert runner._extract_role_from_line(line) is None

    def test_all_known_roles(self, runner):
        for role_name in ROLE_LABELS:
            line = f"TASK [{role_name} : Some task] ***"
            assert runner._extract_role_from_line(line) == role_name


class TestExtractRoleFromLineLaunch:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        r.ws = Path("/tmp/ansible-test")
        r.playbook = "launch_playbook.yml"
        r.role_labels = LAUNCH_ROLE_LABELS
        return r

    def test_launch_role(self, runner):
        line = "TASK [ibl_launch : Configure base domain] ***"
        assert runner._extract_role_from_line(line) == "ibl_launch"

    def test_launch_services_role(self, runner):
        line = "TASK [ibl_launch_services : Update DM services] ***"
        assert runner._extract_role_from_line(line) == "ibl_launch_services"

    def test_all_launch_roles(self, runner):
        for role_name in LAUNCH_ROLE_LABELS:
            line = f"TASK [{role_name} : Some task] ***"
            assert runner._extract_role_from_line(line) == role_name

    def test_setup_only_role_not_recognized(self, runner):
        """Roles from the setup playbook aren't recognized in launch context."""
        line = "TASK [docker : Install Docker CE] ***"
        assert runner._extract_role_from_line(line) is None


class TestRunnerInit:
    def test_default_playbook_and_labels(self, project_state, setup_config):
        runner = AnsibleRunner(project_state, setup_config)
        assert runner.playbook == "playbook.yml"
        assert runner.role_labels is ROLE_LABELS

    def test_custom_playbook_and_labels(self, project_state, setup_config):
        runner = AnsibleRunner(
            project_state, setup_config,
            playbook="launch_playbook.yml",
            role_labels=LAUNCH_ROLE_LABELS,
        )
        assert runner.playbook == "launch_playbook.yml"
        assert runner.role_labels is LAUNCH_ROLE_LABELS


# ---------------------------------------------------------------------------
# Inventory generation
# ---------------------------------------------------------------------------


class TestGenerateInventory:
    def test_generates_inventory_ini(self, project_state, setup_config, tmp_path):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config
        runner.ws = tmp_path

        runner._generate_inventory()

        ini_path = tmp_path / "inventory.ini"
        assert ini_path.exists()
        content = ini_path.read_text()
        assert "[ibl_servers]" in content
        assert "54.123.45.67" in content
        assert "ansible_user=ubuntu" in content
        assert "ansible_python_interpreter=/usr/bin/python3" in content


# ---------------------------------------------------------------------------
# Extra vars
# ---------------------------------------------------------------------------


class TestBuildExtraVars:
    def test_includes_all_fields(self, project_state, setup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config
        runner.role_labels = ROLE_LABELS

        extra = runner._build_extra_vars()
        assert extra["git_access_token"] == "ghp_testtoken123"
        assert extra["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert extra["aws_default_region"] == "us-east-1"
        assert extra["base_domain"] == "example.com"
        assert extra["edx_version"] == "sumac"
        assert extra["env_config"] == "single-server"
        assert extra["cli_ops_release_tag"] == "3.19.0"
        assert extra["is_resetup"] is False
        assert extra["dm_image_tag"] == "4.189.1-ai"
        assert extra["edx_image_tag"] == "sumac.2.4.13"
        assert extra["enable_ai"] is True

    def test_setup_includes_image_tags(self, project_state, setup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config
        runner.role_labels = ROLE_LABELS

        extra = runner._build_extra_vars()
        assert "dm_image_tag" in extra
        assert "edx_image_tag" in extra
        assert "spa_auth_image_tag" in extra
        assert "spa_mentor_image_tag" in extra
        assert "spa_skills_image_tag" in extra

    def test_resetup_omits_image_tags(self, project_state, resetup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = resetup_config
        runner.role_labels = ROLE_LABELS

        extra = runner._build_extra_vars()
        assert extra["is_resetup"] is True
        assert extra["cli_ops_release_tag"] == "3.19.0"
        assert "dm_image_tag" not in extra
        assert "edx_image_tag" not in extra
        assert "spa_auth_image_tag" not in extra
        assert "spa_mentor_image_tag" not in extra
        assert "spa_skills_image_tag" not in extra


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


class TestPreflight:
    def test_ansible_not_installed(self, project_state, setup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config

        with patch("iblai_infra.ansible.runner.shutil.which", return_value=None):
            result = runner._check_ansible_installed()
            assert result is False

    def test_ansible_installed(self, project_state, setup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config

        with patch("iblai_infra.ansible.runner.shutil.which", return_value="/usr/bin/ansible-playbook"):
            result = runner._check_ansible_installed()
            assert result is True


# ---------------------------------------------------------------------------
# Build role table
# ---------------------------------------------------------------------------


class TestBuildRoleTable:
    def test_empty_roles(self):
        from rich.table import Table

        table = AnsibleRunner._build_role_table({})
        assert isinstance(table, Table)

    def test_all_statuses(self):
        roles = {
            "docker": {"label": "Docker Engine", "status": "complete", "elapsed": 30},
            "awscli": {"label": "AWS CLI", "status": "in_progress", "elapsed": 5},
            "python": {"label": "Python Environment", "status": "error", "elapsed": 10},
        }
        table = AnsibleRunner._build_role_table(roles)
        assert table.row_count == 3


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_total_roles_matches_labels(self):
        assert TOTAL_ROLES == len(ROLE_LABELS)

    def test_expected_roles(self):
        expected = {"docker", "awscli", "python", "ibl_cli_ops", "ibl_platform", "ibl_dm", "ibl_edx", "ibl_spa", "final_steps"}
        assert set(ROLE_LABELS.keys()) == expected

    def test_launch_role_labels(self):
        expected = {"ibl_cli_ops", "ibl_launch", "ibl_launch_services", "final_steps"}
        assert set(LAUNCH_ROLE_LABELS.keys()) == expected

    def test_launch_role_labels_count(self):
        assert len(LAUNCH_ROLE_LABELS) == 4


# ---------------------------------------------------------------------------
# SSH connectivity test — all diagnosis paths
# ---------------------------------------------------------------------------


class TestSSHTest:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        return r

    def test_ssh_success(self, runner):
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("iblai_infra.ansible.runner.subprocess.run", return_value=mock_result):
            assert runner._test_ssh() is True

    def test_ssh_permission_denied(self, runner):
        mock_result = MagicMock()
        mock_result.returncode = 255
        mock_result.stderr = "Permission denied (publickey)"

        with patch("iblai_infra.ansible.runner.subprocess.run", return_value=mock_result):
            assert runner._test_ssh() is False

    def test_ssh_connection_refused(self, runner):
        mock_result = MagicMock()
        mock_result.returncode = 255
        mock_result.stderr = "Connection refused"

        with patch("iblai_infra.ansible.runner.subprocess.run", return_value=mock_result):
            assert runner._test_ssh() is False

    def test_ssh_connection_timed_out(self, runner):
        mock_result = MagicMock()
        mock_result.returncode = 255
        mock_result.stderr = "Connection timed out"

        with patch("iblai_infra.ansible.runner.subprocess.run", return_value=mock_result):
            assert runner._test_ssh() is False

    def test_ssh_no_route(self, runner):
        mock_result = MagicMock()
        mock_result.returncode = 255
        mock_result.stderr = "No route to host"

        with patch("iblai_infra.ansible.runner.subprocess.run", return_value=mock_result):
            assert runner._test_ssh() is False

    def test_ssh_other_error(self, runner):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Some unexpected error"

        with patch("iblai_infra.ansible.runner.subprocess.run", return_value=mock_result):
            assert runner._test_ssh() is False

    def test_ssh_empty_stderr(self, runner):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = ""

        with patch("iblai_infra.ansible.runner.subprocess.run", return_value=mock_result):
            assert runner._test_ssh() is False


# ---------------------------------------------------------------------------
# Preflight — combined check
# ---------------------------------------------------------------------------


class TestPreflightCombined:
    def test_both_pass(self, project_state, setup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config

        with (
            patch("iblai_infra.ansible.runner.shutil.which", return_value="/usr/bin/ansible-playbook"),
            patch.object(runner, "_test_ssh", return_value=True),
        ):
            assert runner.preflight() is True

    def test_ansible_missing(self, project_state, setup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config

        with patch("iblai_infra.ansible.runner.shutil.which", return_value=None):
            assert runner.preflight() is False

    def test_ssh_fails(self, project_state, setup_config):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config

        with (
            patch("iblai_infra.ansible.runner.shutil.which", return_value="/usr/bin/ansible-playbook"),
            patch.object(runner, "_test_ssh", return_value=False),
        ):
            assert runner.preflight() is False


# ---------------------------------------------------------------------------
# Environment building
# ---------------------------------------------------------------------------


class TestAnsibleEnv:
    def test_env_variables(self, project_state, setup_config, tmp_path):
        runner = AnsibleRunner.__new__(AnsibleRunner)
        runner.state = project_state
        runner.config = setup_config
        runner.ws = tmp_path

        env = runner._env()
        assert env["ANSIBLE_HOST_KEY_CHECKING"] == "False"
        assert env["ANSIBLE_FORCE_COLOR"] == "false"
        assert "ANSIBLE_CONFIG" in env
        assert "ANSIBLE_STDOUT_CALLBACK" not in env
