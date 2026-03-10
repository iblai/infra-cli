"""Tests for iblai_infra.ansible.runner — line parsing, role detection, error extraction."""

from __future__ import annotations

from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from iblai_infra.ansible.runner import ROLE_LABELS, TOTAL_ROLES, AnsibleRunner


# ---------------------------------------------------------------------------
# Role detection
# ---------------------------------------------------------------------------


class TestExtractRole:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        r.ws = Path("/tmp/ansible-test")
        return r

    def test_role_from_task_role_field(self, runner):
        event = {"task": {"role": "docker", "name": "Install Docker"}}
        assert runner._extract_role(event) == "docker"

    def test_role_from_task_name(self, runner):
        event = {"task": {"name": "awscli : Install AWS CLI"}}
        assert runner._extract_role(event) == "awscli"

    def test_role_from_play_field(self, runner):
        event = {"play": {"role": "awscli"}}
        assert runner._extract_role(event) == "awscli"

    def test_role_from_nested_plays(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {"task": {"role": "python", "name": "Install Python"}}
                    ]
                }
            ]
        }
        assert runner._extract_role(event) == "python"

    def test_unknown_role_returns_none(self, runner):
        event = {"task": {"role": "unknown_role", "name": "Some task"}}
        assert runner._extract_role(event) is None

    def test_empty_event(self, runner):
        assert runner._extract_role({}) is None

    def test_role_name_in_task_name_case_insensitive(self, runner):
        event = {"task": {"name": "Docker : Install docker-ce"}}
        assert runner._extract_role(event) == "docker"

    def test_all_known_roles_detected(self, runner):
        for role_name in ROLE_LABELS:
            event = {"task": {"role": role_name, "name": f"{role_name} task"}}
            assert runner._extract_role(event) == role_name

    def test_python_role_from_name(self, runner):
        event = {"task": {"name": "python : Install pyenv"}}
        assert runner._extract_role(event) == "python"


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


# ---------------------------------------------------------------------------
# Failure detection
# ---------------------------------------------------------------------------


class TestIsFailure:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        return r

    def test_failure_from_stats(self, runner):
        event = {
            "stats": {
                "server1": {"failures": 2, "unreachable": 0},
            }
        }
        assert runner._is_failure(event) is True

    def test_unreachable_from_stats(self, runner):
        event = {
            "stats": {
                "server1": {"failures": 0, "unreachable": 1},
            }
        }
        assert runner._is_failure(event) is True

    def test_success_from_stats(self, runner):
        event = {
            "stats": {
                "server1": {"failures": 0, "unreachable": 0, "ok": 5},
            }
        }
        assert runner._is_failure(event) is False

    def test_failure_from_host_result(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "hosts": {
                                "server1": {"failed": True, "msg": "Package not found"}
                            }
                        }
                    ]
                }
            ]
        }
        assert runner._is_failure(event) is True

    def test_unreachable_from_host_result(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "hosts": {
                                "server1": {"unreachable": True, "msg": "Connection refused"}
                            }
                        }
                    ]
                }
            ]
        }
        assert runner._is_failure(event) is True

    def test_success_event(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "hosts": {
                                "server1": {"changed": True, "msg": "OK"}
                            }
                        }
                    ]
                }
            ]
        }
        assert runner._is_failure(event) is False

    def test_empty_event(self, runner):
        assert runner._is_failure({}) is False


# ---------------------------------------------------------------------------
# Error extraction
# ---------------------------------------------------------------------------


class TestExtractError:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        return r

    def test_error_from_failed_host(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "task": {"name": "Install Docker"},
                            "hosts": {
                                "server1": {"failed": True, "msg": "Package not found"}
                            },
                        }
                    ]
                }
            ]
        }
        msg = runner._extract_error(event)
        assert "Install Docker" in msg
        assert "Package not found" in msg

    def test_error_from_stderr(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "task": {"name": "Run script"},
                            "hosts": {
                                "server1": {
                                    "failed": True,
                                    "msg": "",
                                    "stderr": "Permission denied",
                                }
                            },
                        }
                    ]
                }
            ]
        }
        msg = runner._extract_error(event)
        assert "Permission denied" in msg

    def test_error_from_stats_failures(self, runner):
        event = {
            "stats": {
                "54.123.45.67": {"failures": 3, "unreachable": 0},
            }
        }
        msg = runner._extract_error(event)
        assert "54.123.45.67" in msg
        assert "3 task(s) failed" in msg

    def test_error_from_stats_unreachable(self, runner):
        event = {
            "stats": {
                "54.123.45.67": {"failures": 0, "unreachable": 1},
            }
        }
        msg = runner._extract_error(event)
        assert "unreachable" in msg

    def test_fallback_unknown_error(self, runner):
        event = {}
        msg = runner._extract_error(event)
        assert msg == "Unknown error"


# ---------------------------------------------------------------------------
# _parse_json_line
# ---------------------------------------------------------------------------


class TestAnsibleParseJsonLine:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        return r

    def test_valid_json(self, runner):
        result = runner._parse_json_line('{"plays": []}')
        assert result == {"plays": []}

    def test_empty(self, runner):
        assert runner._parse_json_line("") is None

    def test_invalid_json(self, runner):
        assert runner._parse_json_line("PLAY [all] *****") is None


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

        extra = runner._build_extra_vars()
        assert extra["git_access_token"] == "ghp_testtoken123"
        assert extra["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert extra["aws_default_region"] == "us-east-1"
        assert extra["base_domain"] == "example.com"
        assert extra["edx_version"] == "sumac"
        assert extra["env_config"] == "single-server"


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
        expected = {"docker", "awscli", "python", "ibl_cli_ops", "ibl_platform", "ibl_dm", "ibl_edx"}
        assert set(ROLE_LABELS.keys()) == expected


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


# ---------------------------------------------------------------------------
# Edge cases in role extraction
# ---------------------------------------------------------------------------


class TestExtractRoleEdgeCases:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        return r

    def test_task_with_empty_role(self, runner):
        event = {"task": {"role": "", "name": "Some task"}}
        assert runner._extract_role(event) is None

    def test_play_with_non_dict(self, runner):
        event = {"play": "not a dict"}
        assert runner._extract_role(event) is None

    def test_nested_plays_empty_tasks(self, runner):
        event = {"plays": [{"tasks": []}]}
        assert runner._extract_role(event) is None

    def test_nested_plays_no_role(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {"task": {"name": "Unknown task"}}
                    ]
                }
            ]
        }
        assert runner._extract_role(event) is None

    def test_multiple_roles_in_event(self, runner):
        """First matching role should be returned."""
        event = {
            "task": {"role": "docker", "name": "docker task"},
            "plays": [
                {
                    "tasks": [
                        {"task": {"role": "awscli"}}
                    ]
                }
            ],
        }
        # Should match docker first (from task.role)
        assert runner._extract_role(event) == "docker"


# ---------------------------------------------------------------------------
# Edge cases in failure detection
# ---------------------------------------------------------------------------


class TestIsFailureEdgeCases:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        return r

    def test_zero_failures_in_stats(self, runner):
        event = {"stats": {"host": {"failures": 0, "unreachable": 0}}}
        assert runner._is_failure(event) is False

    def test_multiple_hosts_one_failed(self, runner):
        event = {
            "stats": {
                "host1": {"failures": 0, "unreachable": 0},
                "host2": {"failures": 1, "unreachable": 0},
            }
        }
        assert runner._is_failure(event) is True

    def test_plays_no_hosts(self, runner):
        event = {"plays": [{"tasks": [{"hosts": {}}]}]}
        assert runner._is_failure(event) is False

    def test_host_not_failed(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {"hosts": {"h": {"changed": True, "failed": False}}}
                    ]
                }
            ]
        }
        assert runner._is_failure(event) is False


# ---------------------------------------------------------------------------
# Error extraction edge cases
# ---------------------------------------------------------------------------


class TestExtractErrorEdgeCases:
    @pytest.fixture
    def runner(self, project_state, setup_config):
        r = AnsibleRunner.__new__(AnsibleRunner)
        r.state = project_state
        r.config = setup_config
        return r

    def test_both_msg_and_stderr(self, runner):
        """msg takes priority over stderr."""
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "task": {"name": "task"},
                            "hosts": {
                                "h": {"failed": True, "msg": "Error msg", "stderr": "Error stderr"}
                            },
                        }
                    ]
                }
            ]
        }
        result = runner._extract_error(event)
        assert "Error msg" in result

    def test_no_msg_uses_stderr(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "task": {"name": "task"},
                            "hosts": {
                                "h": {"failed": True, "msg": "", "stderr": "stderr error"}
                            },
                        }
                    ]
                }
            ]
        }
        result = runner._extract_error(event)
        assert "stderr error" in result

    def test_missing_task_name(self, runner):
        event = {
            "plays": [
                {
                    "tasks": [
                        {
                            "task": {},
                            "hosts": {"h": {"failed": True, "msg": "error"}},
                        }
                    ]
                }
            ]
        }
        result = runner._extract_error(event)
        assert "unknown task" in result
