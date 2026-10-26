"""The multi-node commands and their guards on the existing commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from iblai_infra.cli import app
from iblai_infra.env_multinode import load_or_create_state, topology_of
from iblai_infra.models import SetupConfig

runner = CliRunner()


@pytest.fixture(autouse=True)
def _keys_ok():
    with patch("iblai_infra.env_multinode.validate_key_permissions", return_value=True), \
         patch("iblai_infra.env_setup.validate_key_permissions", return_value=True):
        yield


@pytest.fixture
def env_file(tmp_path):
    key = tmp_path / "id_test"
    key.write_text("x")
    f = tmp_path / ".env"
    f.write_text(
        "PROJECT_NAME=demo\nBASE_DOMAIN=example.com\n"
        f"SSH_PRIVATE_KEY_PATH={key}\n"
        "HUB_HOST=10.0.1.10\nAPP_HOSTS=10.0.1.11\nSTORES_HOST=10.0.2.10\nSANDBOX_HOST=10.0.3.10\n"
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\nAWS_SECRET_ACCESS_KEY=secretsecret\n"
        "GIT_TOKEN=test-pat\nCLI_OPS_RELEASE_TAG=7.24.0\n"
        "ADMIN_USERNAME=platform_admin\nADMIN_EMAIL=admin@example.com\nADMIN_PASSWORD=a-long-password\n"
    )
    return f


@pytest.fixture
def mn_state(workspace_root, env_file):
    from iblai_infra.env_utils import load_env_file

    return load_or_create_state(load_env_file(env_file), create=True)


class TestHelp:
    @pytest.mark.parametrize("group, commands", [
        ("app", ["init", "setup", "add", "sync", "verify"]),
        ("stores", ["setup"]),
        ("sandbox", ["setup", "connect"]),
        ("call", ["connect"]),
    ])
    def test_groups_list_their_commands(self, group, commands):
        result = runner.invoke(app, ["infra", group, "--help"])
        assert result.exit_code == 0
        for c in commands:
            assert c in result.output


class TestGuards:
    @pytest.mark.parametrize("command", ["setup", "resetup"])
    def test_single_server_commands_refuse_a_multi_node_project(self, mn_state, command):
        result = runner.invoke(app, ["infra", command, "demo"])
        assert result.exit_code != 0
        assert "multi-node" in result.output

    def test_destroy_only_untracks(self, mn_state):
        with patch("questionary.confirm") as confirm, \
             patch("iblai_infra.terraform.runner.TerraformRunner") as tf:
            confirm.return_value.ask.return_value = True
            result = runner.invoke(app, ["infra", "destroy", "demo"])
        assert result.exit_code == 0
        tf.assert_not_called()
        from iblai_infra.terraform.state import load_state

        assert load_state("demo").status == "destroyed"

    def test_app_add_needs_a_set_up_hub(self, mn_state, env_file):
        result = runner.invoke(app, ["infra", "app", "add", "-f", str(env_file), "--host", "10.0.1.12"])
        assert result.exit_code != 0

    def test_app_add_refuses_a_host_with_another_role(self, mn_state, env_file):
        mn_state.setup_status = "completed"
        from iblai_infra.terraform.state import save_state

        save_state(mn_state)
        result = runner.invoke(app, ["infra", "app", "add", "-f", str(env_file), "--host", "10.0.2.10"])
        assert result.exit_code != 0

    def test_a_missing_env_file_is_reported(self, tmp_path):
        result = runner.invoke(app, ["infra", "stores", "setup", "-f", str(tmp_path / "nope")])
        assert result.exit_code != 0


class TestRuns:
    def _fake_runner(self):
        inst = MagicMock()
        inst.preflight.return_value = True
        inst.run_step.return_value = True
        return inst

    def test_stores_setup_runs_its_playbook_on_the_right_hosts(self, mn_state, env_file):
        inst = self._fake_runner()
        with patch("iblai_infra.ansible.multinode.MultiNodeRunner", return_value=inst) as cls:
            result = runner.invoke(app, ["infra", "stores", "setup", "-f", str(env_file)])
        assert result.exit_code == 0, result.output
        args, kwargs = cls.call_args
        assert args[3] == "stores_setup.yml"
        assert args[1].target_host == "10.0.2.10"
        assert "stores" in kwargs["ssh_groups"]

    def test_app_setup_completes_setup(self, mn_state, env_file):
        inst = self._fake_runner()
        with patch("iblai_infra.ansible.multinode.MultiNodeRunner", return_value=inst) as cls:
            result = runner.invoke(app, ["infra", "app", "setup", "-f", str(env_file)])
        assert result.exit_code == 0, result.output
        assert cls.call_args[0][3] == "app_setup.yml"
        inst.run_step.assert_called_once()
        assert inst.run_step.call_args.kwargs["completes_setup"] is True

    def test_app_add_records_the_node(self, mn_state, env_file):
        from iblai_infra.terraform.state import load_state, save_state

        mn_state.setup_status = "completed"
        save_state(mn_state)
        inst = self._fake_runner()
        with patch("iblai_infra.ansible.multinode.MultiNodeRunner", return_value=inst) as cls:
            result = runner.invoke(app, ["infra", "app", "add", "-f", str(env_file), "--host", "10.0.1.12"])
        assert result.exit_code == 0, result.output
        assert cls.call_args.kwargs["new_apps"] == ["10.0.1.12"]
        topo = topology_of(load_state("demo"))
        assert topo.apps == ["10.0.1.11", "10.0.1.12"]
        assert topo.node_ids["10.0.1.12"] == "demo-app-3"

    def test_a_failed_step_exits_non_zero(self, mn_state, env_file):
        inst = self._fake_runner()
        inst.run_step.return_value = False
        with patch("iblai_infra.ansible.multinode.MultiNodeRunner", return_value=inst):
            result = runner.invoke(app, ["infra", "app", "sync", "-f", str(env_file)])
        assert result.exit_code != 0

    def test_call_connect_needs_a_call_server(self, mn_state, env_file):
        from iblai_infra.terraform.state import save_state

        mn_state.setup_status = "completed"
        save_state(mn_state)
        result = runner.invoke(app, ["infra", "call", "connect", "-f", str(env_file)])
        assert result.exit_code != 0


class TestFeatures:
    def test_a_feature_on_multi_node_runs_on_the_hub_then_syncs(self, mn_state):
        from iblai_infra.features._common import apply_feature

        config = SetupConfig.for_feature(mn_state)
        assert config.target_host == "10.0.1.10"
        inst = MagicMock()
        inst.preflight.return_value = True
        inst.run_partial.return_value = True
        with patch("iblai_infra.ansible.multinode.MultiNodeRunner", return_value=inst) as cls:
            assert apply_feature(mn_state, config, ["smtp"], {"smtp_config": "SMTP"}, "SMTP")
        assert cls.call_args[0][3] == "features.yml"
        inst.run_partial.assert_called_once()

    def test_a_single_server_feature_still_uses_the_base_runner(self, project_state, setup_config):
        from iblai_infra.features._common import apply_feature

        inst = MagicMock()
        inst.preflight.return_value = True
        inst.run_partial.return_value = True
        with patch("iblai_infra.ansible.runner.AnsibleRunner", return_value=inst) as base, \
             patch("iblai_infra.ansible.multinode.MultiNodeRunner") as multi:
            assert apply_feature(project_state, setup_config, ["smtp"], {}, "SMTP")
        base.assert_called_once()
        multi.assert_not_called()

    def test_features_playbook_carries_every_feature_tag(self):
        import yaml
        from pathlib import Path

        pb = Path(__file__).resolve().parents[1] / "src/iblai_infra/ansible/templates/multi-node/features.yml"
        plays = yaml.safe_load(pb.read_text())
        tags = {t for r in plays[1]["roles"] if isinstance(r, dict) for t in r.get("tags", [])}
        assert {"smtp", "platform", "stripe", "google_sso", "microsoft_sso"} <= tags
        assert set(plays[2]["tags"]) >= tags | {"llm"}
