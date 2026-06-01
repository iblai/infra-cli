"""Tests for iblai_infra.features.waf — the `iblai infra waf` subgroup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from iblai_infra.features.waf import (
    _eligible_states,
    _load_and_guard_waf_target,
    _resolve_project_name,
    waf_app,
)
from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DNSConfig,
    DeploymentType,
    Environment,
    InfraConfig,
    NetworkConfig,
    ProjectState,
    SSHConfig,
    SSHKeyMethod,
    WAFConfig,
)

cli = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_state(
    name: str = "wafproj",
    *,
    deployment_type: DeploymentType = DeploymentType.SINGLE,
    status: str = "created",
    provider: str = "aws",
    waf: WAFConfig | None = None,
    workspace_path: str | None = None,
) -> ProjectState:
    cfg = InfraConfig(
        project_name=name,
        environment=Environment.STAGING,
        deployment_type=deployment_type,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY,
            access_key_id="AKIAEXAMPLE",
            secret_access_key="SK",
            region="us-east-1",
        ),
        network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="203.0.113.1"),
        compute=ComputeConfig(volume_size=100),
        ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name=f"{name}-stg"),
        certificates=CertificateConfig(method=CertMethod.NONE),
        dns=DNSConfig(base_domain="example.com"),
        waf=waf,
    )
    return ProjectState(
        name=name,
        provider=provider,
        status=status,
        config=cfg,
        outputs={"instance_public_ip": "1.2.3.4"},
        workspace_path=workspace_path or f"/tmp/iblai-fake/{name}",
    )


@pytest.fixture
def fake_workspace(tmp_path: Path) -> Path:
    """Workspace dir with a stub main.tf so the guard passes."""
    (tmp_path / "main.tf").write_text("# stub")
    return tmp_path


# ---------------------------------------------------------------------------
# Guard / resolver helpers
# ---------------------------------------------------------------------------


class TestLoadAndGuardWafTarget:
    def test_missing_project(self):
        with patch("iblai_infra.features.waf.load_state", return_value=None):
            with pytest.raises(typer.Exit):
                _load_and_guard_waf_target("nope")

    def test_bootstrap_rejected(self, fake_workspace):
        s = _make_state(provider="bootstrap", workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            with pytest.raises(typer.Exit):
                _load_and_guard_waf_target(s.name)

    def test_multi_server_rejected(self, fake_workspace):
        s = _make_state(
            deployment_type=DeploymentType.MULTI,
            workspace_path=str(fake_workspace),
        )
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            with pytest.raises(typer.Exit):
                _load_and_guard_waf_target(s.name)

    def test_status_not_created_rejected(self, fake_workspace):
        s = _make_state(status="failed", workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            with pytest.raises(typer.Exit):
                _load_and_guard_waf_target(s.name)

    def test_missing_workspace_rejected(self, tmp_path):
        # workspace dir missing entirely
        s = _make_state(workspace_path=str(tmp_path / "nonexistent"))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            with pytest.raises(typer.Exit):
                _load_and_guard_waf_target(s.name)

    def test_missing_main_tf_rejected(self, tmp_path):
        # workspace dir exists but no main.tf
        s = _make_state(workspace_path=str(tmp_path))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            with pytest.raises(typer.Exit):
                _load_and_guard_waf_target(s.name)

    def test_happy_path(self, fake_workspace):
        s = _make_state(workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            result = _load_and_guard_waf_target(s.name)
            assert result is s


class TestResolveProjectName:
    def test_passthrough_when_given(self):
        assert _resolve_project_name("foo") == "foo"

    def test_picks_only_eligible(self, fake_workspace):
        only = _make_state(name="only", workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf._eligible_states", return_value=[only]):
            assert _resolve_project_name(None) == "only"

    def test_no_eligible_raises(self):
        with patch("iblai_infra.features.waf._eligible_states", return_value=[]):
            with pytest.raises(typer.Exit):
                _resolve_project_name(None)

    def test_multiple_eligible_prompts_select(self, fake_workspace):
        a = _make_state(name="a", workspace_path=str(fake_workspace))
        b = _make_state(name="b", workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf._eligible_states", return_value=[a, b]), \
             patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "b"
            assert _resolve_project_name(None) == "b"


class TestEligibleStates:
    def test_filters_status_and_provider_and_deployment(self, fake_workspace):
        all_states = [
            _make_state(name="ok", workspace_path=str(fake_workspace)),
            _make_state(name="bootstrap", provider="bootstrap"),
            _make_state(name="failed", status="failed"),
            _make_state(name="multi", deployment_type=DeploymentType.MULTI),
        ]
        with patch("iblai_infra.features.waf.list_all_states", return_value=all_states):
            eligible = _eligible_states()
        assert [s.name for s in eligible] == ["ok"]


# ---------------------------------------------------------------------------
# enable
# ---------------------------------------------------------------------------


class TestWafEnable:
    def test_enable_fresh(self, fake_workspace):
        s = _make_state(workspace_path=str(fake_workspace))
        new_outputs = {
            "waf_web_acl_arn": "arn:aws:wafv2:us-east-1:1:regional/webacl/x/y",
            "waf_ip_set_arn": "arn:aws:wafv2:us-east-1:1:regional/ipset/x/y",
        }
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.save_state") as mock_save, \
             patch("iblai_infra.features.waf._prompt_waf_ips", return_value=["203.0.113.7"]), \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls, \
             patch("questionary.confirm") as mock_confirm:
            # final confirm = yes (only one confirm in fresh path)
            mock_confirm.return_value.ask.return_value = True
            mock_runner = MagicMock()
            mock_runner.reapply.return_value = new_outputs
            mock_tf_cls.return_value = mock_runner

            result = cli.invoke(waf_app, ["enable", s.name])

        assert result.exit_code == 0, result.stdout
        # State got mutated then saved at least twice (before reapply + after)
        assert s.config.waf is not None
        assert s.config.waf.enabled is True
        assert s.config.waf.allowed_ips == ["203.0.113.7/32"]
        # Outputs merged
        assert s.outputs["waf_web_acl_arn"] == new_outputs["waf_web_acl_arn"]
        mock_runner.reapply.assert_called_once()
        assert mock_save.called

    def test_enable_already_enabled_prompts_update(self, fake_workspace):
        s = _make_state(
            workspace_path=str(fake_workspace),
            waf=WAFConfig(enabled=True, allowed_ips=["10.0.0.0/8"]),
        )
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.save_state"), \
             patch("iblai_infra.features.waf._prompt_waf_ips", return_value=["10.0.0.0/8", "203.0.113.7"]) as mock_prompt, \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls, \
             patch("questionary.confirm") as mock_confirm:
            # Two confirms: "Update the allowlist?" (yes) and "Apply this change?" (yes)
            mock_confirm.return_value.ask.return_value = True
            mock_tf_cls.return_value.reapply.return_value = {}
            result = cli.invoke(waf_app, ["enable", s.name])

        assert result.exit_code == 0, result.stdout
        # The pre-fill default for the IP prompt was the existing list
        mock_prompt.assert_called_once_with(default=["10.0.0.0/8"])
        assert s.config.waf.allowed_ips == ["10.0.0.0/8", "203.0.113.7/32"]

    def test_enable_already_enabled_decline_update(self, fake_workspace):
        s = _make_state(
            workspace_path=str(fake_workspace),
            waf=WAFConfig(enabled=True, allowed_ips=["10.0.0.0/8"]),
        )
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.save_state") as mock_save, \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls, \
             patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = False  # decline update
            result = cli.invoke(waf_app, ["enable", s.name])

        assert result.exit_code == 0
        mock_tf_cls.assert_not_called()
        mock_save.assert_not_called()  # No mutation, no save
        # Original config untouched
        assert s.config.waf.allowed_ips == ["10.0.0.0/8"]

    def test_enable_final_confirm_no_aborts(self, fake_workspace):
        s = _make_state(workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.save_state"), \
             patch("iblai_infra.features.waf._prompt_waf_ips", return_value=["203.0.113.7"]), \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls, \
             patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = False  # final confirm = no
            result = cli.invoke(waf_app, ["enable", s.name])

        assert result.exit_code != 0  # ui.abort exits non-zero
        mock_tf_cls.assert_not_called()

    def test_enable_rejects_multi_server(self, fake_workspace):
        s = _make_state(
            deployment_type=DeploymentType.MULTI,
            workspace_path=str(fake_workspace),
        )
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            result = cli.invoke(waf_app, ["enable", s.name])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# enable-env
# ---------------------------------------------------------------------------


class TestWafEnableEnv:
    def test_happy_path(self, fake_workspace, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("WAF_ALLOWED_IPS=203.0.113.7,10.0.0.0/16\n")
        s = _make_state(workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.save_state"), \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls:
            mock_tf_cls.return_value.reapply.return_value = {"waf_web_acl_arn": "arn:..."}
            result = cli.invoke(waf_app, ["enable-env", s.name, "-f", str(env_file)])
        assert result.exit_code == 0, result.stdout
        assert s.config.waf.enabled is True
        assert s.config.waf.allowed_ips == ["203.0.113.7/32", "10.0.0.0/16"]

    def test_missing_env_file(self, tmp_path):
        result = cli.invoke(waf_app, ["enable-env", "any", "-f", str(tmp_path / "nope.env")])
        assert result.exit_code != 0

    def test_missing_waf_allowed_ips(self, fake_workspace, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# WAF_ALLOWED_IPS=\n")
        s = _make_state(workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            result = cli.invoke(waf_app, ["enable-env", s.name, "-f", str(env_file)])
        assert result.exit_code != 0

    def test_invalid_ip_in_env(self, fake_workspace, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("WAF_ALLOWED_IPS=not-an-ip\n")
        s = _make_state(workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            result = cli.invoke(waf_app, ["enable-env", s.name, "-f", str(env_file)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


class TestWafDisable:
    def test_disable_with_yes(self, fake_workspace):
        s = _make_state(
            workspace_path=str(fake_workspace),
            waf=WAFConfig(enabled=True, allowed_ips=["203.0.113.7"]),
        )
        s.outputs["waf_web_acl_arn"] = "arn:aws:wafv2:..."
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.save_state"), \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls:
            mock_tf_cls.return_value.reapply.return_value = {}
            result = cli.invoke(waf_app, ["disable", s.name, "--yes"])

        assert result.exit_code == 0, result.stdout
        assert s.config.waf.enabled is False
        assert "waf_web_acl_arn" not in (s.outputs or {})

    def test_disable_already_disabled_noop(self, fake_workspace):
        s = _make_state(workspace_path=str(fake_workspace))  # waf=None
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls:
            result = cli.invoke(waf_app, ["disable", s.name, "--yes"])
        assert result.exit_code == 0
        mock_tf_cls.assert_not_called()

    def test_disable_interactive_confirm_no(self, fake_workspace):
        s = _make_state(
            workspace_path=str(fake_workspace),
            waf=WAFConfig(enabled=True, allowed_ips=["203.0.113.7"]),
        )
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls, \
             patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = False
            result = cli.invoke(waf_app, ["disable", s.name])
        assert result.exit_code != 0  # ui.abort
        mock_tf_cls.assert_not_called()

    def test_disable_interactive_confirm_yes(self, fake_workspace):
        s = _make_state(
            workspace_path=str(fake_workspace),
            waf=WAFConfig(enabled=True, allowed_ips=["203.0.113.7"]),
        )
        with patch("iblai_infra.features.waf.load_state", return_value=s), \
             patch("iblai_infra.features.waf.save_state"), \
             patch("iblai_infra.features.waf.TerraformRunner") as mock_tf_cls, \
             patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            mock_tf_cls.return_value.reapply.return_value = {}
            result = cli.invoke(waf_app, ["disable", s.name])
        assert result.exit_code == 0
        assert s.config.waf.enabled is False


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestWafStatus:
    def test_status_with_name_disabled(self, fake_workspace):
        s = _make_state(workspace_path=str(fake_workspace))
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            result = cli.invoke(waf_app, ["status", s.name])
        assert result.exit_code == 0
        assert "Disabled" in result.stdout

    def test_status_with_name_enabled(self, fake_workspace):
        s = _make_state(
            workspace_path=str(fake_workspace),
            waf=WAFConfig(enabled=True, allowed_ips=["203.0.113.7"]),
        )
        s.outputs["waf_web_acl_arn"] = "arn:aws:wafv2:..."
        with patch("iblai_infra.features.waf.load_state", return_value=s):
            result = cli.invoke(waf_app, ["status", s.name])
        assert result.exit_code == 0
        assert "Enabled" in result.stdout
        # IP is rendered (1 entry shown)
        assert "203.0.113.7/32" in result.stdout

    def test_status_missing_project(self):
        with patch("iblai_infra.features.waf.load_state", return_value=None):
            result = cli.invoke(waf_app, ["status", "nope"])
        assert result.exit_code != 0

    def test_status_no_name_table(self, fake_workspace):
        a = _make_state(name="a", workspace_path=str(fake_workspace))
        b = _make_state(
            name="b",
            workspace_path=str(fake_workspace),
            waf=WAFConfig(enabled=True, allowed_ips=["1.2.3.4"]),
        )
        with patch("iblai_infra.features.waf._eligible_states", return_value=[a, b]):
            result = cli.invoke(waf_app, ["status"])
        assert result.exit_code == 0
        assert "a" in result.stdout and "b" in result.stdout

    def test_status_no_name_no_eligible(self):
        with patch("iblai_infra.features.waf._eligible_states", return_value=[]):
            result = cli.invoke(waf_app, ["status"])
        assert result.exit_code == 0  # info message, not error
