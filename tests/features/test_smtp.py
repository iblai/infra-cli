"""Tests for the post-setup SMTP feature and the plumbing it rides on."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from iblai_infra.features._common import confirm_restart, load_feature_target
from iblai_infra.features.smtp import AFFECTED_SERVICES, SMTP_TAGS
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
    SetupConfig,
)


def _infra_config(**kwargs) -> InfraConfig:
    defaults = dict(
        project_name="acme",
        environment=Environment.PROD,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY, region="us-east-1",
            access_key_id="AK", secret_access_key="SK",
        ),
        network=NetworkConfig(vpn_ip="1.2.3.4"),
        compute=ComputeConfig(),
        ssh=SSHConfig(
            method=SSHKeyMethod.GENERATE, key_name="k",
            private_key_path=Path("/tmp/key.pem"),
        ),
        certificates=CertificateConfig(method=CertMethod.NONE),
        dns=DNSConfig(base_domain="acme.example.com"),
    )
    defaults.update(kwargs)
    return InfraConfig(**defaults)


def _state(**kwargs) -> ProjectState:
    defaults = dict(
        name="acme",
        config=_infra_config(),
        outputs={"instance_public_ip": "192.0.2.10"},
        workspace_path="/tmp/ws",
        status="created",
        setup_status="completed",
    )
    defaults.update(kwargs)
    return ProjectState(**defaults)


# ---------------------------------------------------------------------------
# SetupConfig.for_feature
# ---------------------------------------------------------------------------


class TestForFeature:
    def test_recovers_connection_details_from_state(self):
        cfg = SetupConfig.for_feature(_state())
        assert cfg.target_host == "192.0.2.10"
        assert cfg.ssh_private_key_path == Path("/tmp/key.pem")
        assert cfg.base_domain == "acme.example.com"

    def test_leaves_credentials_empty(self):
        """The tagged roles read none of these; operators shouldn't be asked."""
        cfg = SetupConfig.for_feature(_state())
        assert cfg.git_access_token == ""
        assert cfg.aws_access_key_id == ""
        assert cfg.aws_secret_access_key == ""

    def test_overrides_are_applied(self):
        cfg = SetupConfig.for_feature(
            _state(), smtp_enabled=True, smtp_host="smtp.example.com", restart_services=True
        )
        assert cfg.smtp_enabled is True
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.restart_services is True

    def test_restart_defaults_off(self):
        """A full setup starts services afterwards, so it must not restart."""
        assert SetupConfig.for_feature(_state()).restart_services is False

    def test_missing_ip_is_rejected(self):
        with pytest.raises(ValueError, match="no instance IP"):
            SetupConfig.for_feature(_state(outputs={}))

    def test_missing_ssh_key_is_rejected(self):
        cfg = _infra_config(
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k", private_key_path=None)
        )
        with pytest.raises(ValueError, match="no SSH key"):
            SetupConfig.for_feature(_state(config=cfg))


# ---------------------------------------------------------------------------
# Target guards
# ---------------------------------------------------------------------------


class TestLoadFeatureTarget:
    def test_accepts_a_set_up_environment(self):
        st = _state()
        with patch("iblai_infra.terraform.state.load_state", return_value=st):
            assert load_feature_target("acme") is st

    def test_unknown_environment_exits(self):
        with patch("iblai_infra.terraform.state.load_state", return_value=None):
            with pytest.raises(typer.Exit):
                load_feature_target("nope")

    def test_destroyed_environment_exits(self):
        with patch("iblai_infra.terraform.state.load_state", return_value=_state(status="destroyed")):
            with pytest.raises(typer.Exit):
                load_feature_target("acme")

    @pytest.mark.parametrize("setup_status", [None, "pending", "failed", "running"])
    def test_environment_without_completed_setup_exits(self, setup_status):
        """Optional features are added after setup, not instead of it."""
        with patch("iblai_infra.terraform.state.load_state", return_value=_state(setup_status=setup_status)):
            with pytest.raises(typer.Exit):
                load_feature_target("acme")


# ---------------------------------------------------------------------------
# Restart confirmation
# ---------------------------------------------------------------------------


class TestConfirmRestart:
    def test_no_restart_flag_wins_without_prompting(self):
        with patch("questionary.confirm") as q:
            assert confirm_restart(AFFECTED_SERVICES, no_restart=True, assume_yes=False) is False
            q.assert_not_called()

    def test_assume_yes_skips_the_prompt(self):
        with patch("questionary.confirm") as q:
            assert confirm_restart(AFFECTED_SERVICES, no_restart=False, assume_yes=True) is True
            q.assert_not_called()

    def test_no_restart_beats_assume_yes(self):
        assert confirm_restart(AFFECTED_SERVICES, no_restart=True, assume_yes=True) is False

    def test_prompts_and_honours_the_answer(self):
        with patch("questionary.confirm") as q:
            q.return_value.ask.return_value = False
            assert confirm_restart(AFFECTED_SERVICES, no_restart=False, assume_yes=False) is False
            q.assert_called_once()


# ---------------------------------------------------------------------------
# run_partial
# ---------------------------------------------------------------------------


class TestRunPartial:
    def _runner(self):
        from iblai_infra.ansible.runner import AnsibleRunner

        return AnsibleRunner(_state(), SetupConfig.for_feature(_state()))

    def test_passes_tags_and_clears_them_after(self):
        runner = self._runner()
        seen = {}

        def fake(steps, progress, task_id, completed):
            seen["tags"] = runner.tags
            return True, 1

        with patch.object(runner, "_run_ansible", side_effect=fake), \
             patch.object(runner, "_print_final_table"):
            assert runner.run_partial(["smtp"]) is True

        assert seen["tags"] == ["smtp"]
        assert runner.tags is None  # not left set for a later full run

    def test_tags_cleared_even_when_the_run_raises(self):
        runner = self._runner()
        with patch.object(runner, "_run_ansible", side_effect=RuntimeError("boom")), \
             patch.object(runner, "_print_final_table"):
            with pytest.raises(RuntimeError):
                runner.run_partial(["smtp"])
        assert runner.tags is None

    def test_failure_is_reported(self):
        runner = self._runner()
        with patch.object(runner, "_run_ansible", return_value=(False, 0)), \
             patch.object(runner, "_print_final_table"):
            assert runner.run_partial(["smtp"]) is False

    def test_does_not_touch_setup_status(self):
        """Adding a feature is not setup; a failure must not un-provision."""
        runner = self._runner()
        runner.state.setup_status = "completed"
        with patch.object(runner, "_run_ansible", return_value=(False, 0)), \
             patch.object(runner, "_print_final_table"):
            runner.run_partial(["smtp"])
        assert runner.state.setup_status == "completed"


# ---------------------------------------------------------------------------
# read_config_values
# ---------------------------------------------------------------------------


class TestReadConfigValues:
    def _runner(self):
        from iblai_infra.ansible.runner import AnsibleRunner

        return AnsibleRunner(_state(), SetupConfig.for_feature(_state()))

    def test_parses_and_unquotes(self):
        runner = self._runner()
        out = MagicMock(returncode=0, stdout="IBL_SMTP_HOST='smtp.example.com'\nIBL_SMTP_PORT=587\n")
        with patch("subprocess.run", return_value=out):
            values = runner.read_config_values(["IBL_SMTP_HOST", "IBL_SMTP_PORT"])
        assert values == {"IBL_SMTP_HOST": "smtp.example.com", "IBL_SMTP_PORT": "587"}

    def test_unreachable_host_returns_none(self):
        runner = self._runner()
        with patch("subprocess.run", return_value=MagicMock(returncode=255, stdout="")):
            assert runner.read_config_values(["IBL_SMTP_HOST"]) is None

    def test_timeout_returns_none(self):
        import subprocess as sp

        runner = self._runner()
        with patch("subprocess.run", side_effect=sp.TimeoutExpired("ssh", 60)):
            assert runner.read_config_values(["IBL_SMTP_HOST"]) is None

    def test_unrequested_keys_are_ignored(self):
        runner = self._runner()
        out = MagicMock(returncode=0, stdout="IBL_SMTP_HOST='a'\nSOMETHING_ELSE='b'\n")
        with patch("subprocess.run", return_value=out):
            assert runner.read_config_values(["IBL_SMTP_HOST"]) == {"IBL_SMTP_HOST": "a"}


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_only_the_smtp_role_is_targeted(self):
        assert SMTP_TAGS == ["smtp"]

    def test_playbook_tags_the_optional_roles(self):
        """The tags run_partial relies on must exist in the shipped playbook."""
        import yaml

        playbook = (
            Path(__file__).resolve().parents[2]
            / "src/iblai_infra/ansible/templates/single-server/playbook.yml"
        )
        roles = yaml.safe_load(playbook.read_text())[0]["roles"]
        tagged = {
            r["role"]: r["tags"] for r in roles if isinstance(r, dict) and r.get("tags")
        }
        assert tagged["smtp_config"] == ["smtp"]
        # The rest are tagged ready for the remaining feature commands.
        for role, tag in [
            ("stripe_config", "stripe"),
            ("google_sso_config", "google_sso"),
            ("microsoft_sso_config", "microsoft_sso"),
            ("ibl_tenant_platform", "platform"),
        ]:
            assert tagged[role] == [tag]

    def test_untagged_roles_still_run_in_a_full_setup(self):
        """Tagging must not change the default behaviour of `setup`."""
        import yaml

        playbook = (
            Path(__file__).resolve().parents[2]
            / "src/iblai_infra/ansible/templates/single-server/playbook.yml"
        )
        roles = yaml.safe_load(playbook.read_text())[0]["roles"]
        names = [r["role"] if isinstance(r, dict) else r for r in roles]
        assert names[:5] == ["docker", "awscli", "python", "ibl_cli_ops", "ibl_platform"]
        assert len(names) == 16
