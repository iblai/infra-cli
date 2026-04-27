"""Tests for iblai_infra.prompts.setup — SSH key resolution, key permissions, prompt flow."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

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
from iblai_infra.prompts.setup import (
    _resolve_ssh_key,
    _validate_key_permissions,
)


# ---------------------------------------------------------------------------
# _resolve_ssh_key — all SSH method × key state combinations
# ---------------------------------------------------------------------------


class TestResolveSSHKey:
    def _make_state(self, method, private_key_path=None, key_name="test-key") -> ProjectState:
        return ProjectState(
            name="test",
            config=InfraConfig(
                project_name="test",
                environment=Environment.DEV,
                credentials=AWSCredentials(method=AuthMethod.ACCESS_KEY, region="us-east-1",
                                           access_key_id="AK", secret_access_key="SK"),
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(
                    method=method,
                    key_name=key_name,
                    private_key_path=private_key_path,
                ),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            ),
            workspace_path="/tmp/test",
            outputs={"instance_public_ip": "1.2.3.4"},
        )

    def test_generate_key_exists(self, tmp_path):
        key_path = tmp_path / "key.pem"
        key_path.write_text("private-key-content")
        state = self._make_state(SSHKeyMethod.GENERATE, private_key_path=key_path)
        result = _resolve_ssh_key(state)
        assert result == key_path

    def test_generate_key_deleted(self, tmp_path):
        key_path = tmp_path / "deleted-key.pem"
        # Don't create the file — simulate it being deleted
        state = self._make_state(SSHKeyMethod.GENERATE, private_key_path=key_path)
        result = _resolve_ssh_key(state)
        assert result is None

    def test_generate_no_path_stored(self):
        state = self._make_state(SSHKeyMethod.GENERATE, private_key_path=None)
        result = _resolve_ssh_key(state)
        assert result is None

    def test_existing_file_always_none(self, tmp_path):
        """EXISTING_FILE method never has a private key stored."""
        key_path = tmp_path / "key.pem"
        key_path.write_text("key")
        state = self._make_state(SSHKeyMethod.EXISTING_FILE, private_key_path=key_path)
        result = _resolve_ssh_key(state)
        assert result is None

    def test_aws_keypair_always_none(self):
        """AWS_KEYPAIR method never has a local private key."""
        state = self._make_state(SSHKeyMethod.AWS_KEYPAIR)
        result = _resolve_ssh_key(state)
        assert result is None


# ---------------------------------------------------------------------------
# _validate_key_permissions — all permission states
# ---------------------------------------------------------------------------


class TestValidateKeyPermissions:
    def test_correct_permissions_600(self, tmp_path):
        key = tmp_path / "key.pem"
        key.write_text("key")
        key.chmod(0o600)
        assert _validate_key_permissions(key) is True

    def test_too_open_permissions_644(self, tmp_path):
        key = tmp_path / "key.pem"
        key.write_text("key")
        key.chmod(0o644)
        result = _validate_key_permissions(key)
        assert result is True
        # Should have fixed permissions
        assert (key.stat().st_mode & 0o777) == 0o600

    def test_too_open_permissions_755(self, tmp_path):
        key = tmp_path / "key.pem"
        key.write_text("key")
        key.chmod(0o755)
        result = _validate_key_permissions(key)
        assert result is True
        assert (key.stat().st_mode & 0o777) == 0o600

    def test_already_restrictive_400(self, tmp_path):
        key = tmp_path / "key.pem"
        key.write_text("key")
        key.chmod(0o400)
        result = _validate_key_permissions(key)
        assert result is True

    def test_nonexistent_file(self, tmp_path):
        key = tmp_path / "nonexistent.pem"
        result = _validate_key_permissions(key)
        assert result is False

    def test_world_readable_777(self, tmp_path):
        key = tmp_path / "key.pem"
        key.write_text("key")
        key.chmod(0o777)
        result = _validate_key_permissions(key)
        assert result is True
        assert (key.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# prompt_setup — questionary-mocked flow
# ---------------------------------------------------------------------------


class TestPromptSetup:
    def _make_state(self, tmp_path, ssh_method=SSHKeyMethod.GENERATE,
                    access_key_id="AKIA", secret_access_key="SECRET"):
        key_path = tmp_path / "key.pem"
        key_path.write_text("key-content")
        key_path.chmod(0o600)

        return ProjectState(
            name="test",
            config=InfraConfig(
                project_name="test",
                environment=Environment.DEV,
                credentials=AWSCredentials(
                    method=AuthMethod.ACCESS_KEY,
                    region="us-east-1",
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                ),
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(
                    method=ssh_method,
                    key_name="test-key",
                    private_key_path=key_path if ssh_method == SSHKeyMethod.GENERATE else None,
                ),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            ),
            workspace_path=str(tmp_path),
            outputs={"instance_public_ip": "54.1.2.3"},
        )

    def test_full_flow_reuse_credentials(self, tmp_path):
        """Test the full flow with GENERATE key and reusing provisioning AWS credentials."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled, stripe_enabled, reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, False, False, False, True]
            mock_text.return_value.ask.side_effect = ["3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_setup(state)

        assert config.edx_version == "sumac"
        assert config.env_config == "single-server"
        assert config.cli_ops_release_tag == "3.19.0"
        assert config.enable_ai is True
        assert config.smtp_enabled is False
        assert config.aws_access_key_id == "AKIA"
        assert config.aws_secret_access_key == "SECRET"
        assert config.git_access_token == "ghp_testtoken"
        assert config.target_host == "54.1.2.3"
        assert config.base_domain == "example.com"
        assert config.admin_username == "ibl_admin"
        assert config.admin_email == "admin@example.com"
        assert config.admin_password == "Admin1234"

    def test_full_flow_new_credentials(self, tmp_path):
        """Test the flow where user declines reusing credentials."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "NEW_SECRET", "sk-test-key", "Admin1234"]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled, stripe_enabled, don't reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, False, False, False, False]
            mock_text.return_value.ask.side_effect = ["3.19.0", "NEW_ACCESS_KEY", "ibl_admin", "admin@example.com"]

            config = prompt_setup(state)

        assert config.edx_version == "sumac"
        assert config.env_config == "single-server"
        assert config.cli_ops_release_tag == "3.19.0"
        assert config.enable_ai is True
        assert config.aws_access_key_id == "NEW_ACCESS_KEY"
        assert config.aws_secret_access_key == "NEW_SECRET"
        assert config.git_access_token == "ghp_testtoken"

    def test_flow_no_access_keys_prompts_directly(self, tmp_path):
        """When provisioning used profile auth, no reuse prompt — goes straight to new keys."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path, access_key_id=None, secret_access_key=None)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "SECRET", "", "Admin1234"]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled, stripe_enabled (no reuse prompt when no access keys)
            mock_confirm.return_value.ask.side_effect = [True, True, False, False]
            mock_text.return_value.ask.side_effect = ["3.19.0", "ACCESS_KEY", "ibl_admin", "admin@example.com"]

            config = prompt_setup(state)

        assert config.edx_version == "sumac"
        assert config.env_config == "single-server"
        assert config.cli_ops_release_tag == "3.19.0"
        assert config.git_access_token == "ghp_testtoken"

    def test_ssh_key_not_found_prompts(self, tmp_path):
        """When SSH key was deleted after provisioning, user is prompted for key path."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path)
        Path(state.config.ssh.private_key_path).unlink()

        new_key = tmp_path / "new-key.pem"
        new_key.write_text("new-key-content")
        new_key.chmod(0o600)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.path") as mock_path,
            patch("questionary.text") as mock_text,
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled, stripe_enabled, reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, False, False, False, True]
            mock_path.return_value.ask.return_value = str(new_key)
            mock_text.return_value.ask.side_effect = ["3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_setup(state)

        assert config.ssh_private_key_path == new_key

    def test_existing_file_method_prompts_for_key(self, tmp_path):
        """EXISTING_FILE SSH method always prompts for private key path."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path, ssh_method=SSHKeyMethod.EXISTING_FILE)

        key = tmp_path / "private.pem"
        key.write_text("key")
        key.chmod(0o600)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.path") as mock_path,
            patch("questionary.text") as mock_text,
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled, stripe_enabled, reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, False, False, False, True]
            mock_path.return_value.ask.return_value = str(key)
            mock_text.return_value.ask.side_effect = ["3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_setup(state)

        assert config.ssh_private_key_path == key

    def test_aws_keypair_method_prompts_for_key(self, tmp_path):
        """AWS_KEYPAIR SSH method always prompts for matching private key."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path, ssh_method=SSHKeyMethod.AWS_KEYPAIR)

        key = tmp_path / "aws-key.pem"
        key.write_text("key")
        key.chmod(0o600)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.path") as mock_path,
            patch("questionary.text") as mock_text,
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled, stripe_enabled, reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, False, False, False, True]
            mock_path.return_value.ask.return_value = str(key)
            mock_text.return_value.ask.side_effect = ["3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_setup(state)

        assert config.ssh_private_key_path == key

    def test_full_flow_smtp_enabled(self, tmp_path):
        """When operator answers yes to SMTP, all 7 fields are collected."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
        ):
            # passwords (questionary.password): smtp_password (step 2), GitHub token (step 3), OpenAI (skip), admin password
            mock_password.return_value.ask.side_effect = [
                "smtp-secret-pw",
                "ghp_testtoken",
                "",
                "Admin1234",
            ]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled,
            #          smtp_use_tls, smtp_use_ssl, stripe_enabled, reuse_credentials
            mock_confirm.return_value.ask.side_effect = [
                True, False, True, True, False, False, True,
            ]
            # texts: cli_ops_tag, smtp_host, smtp_port, smtp_username,
            #        smtp_sender_email, admin_username, admin_email
            mock_text.return_value.ask.side_effect = [
                "3.19.0",
                "email-smtp.us-east-1.amazonaws.com",
                "587",
                "AKIATESTUSER",
                "noreply@example.com",
                "ibl_admin",
                "admin@example.com",
            ]

            config = prompt_setup(state)

        assert config.smtp_enabled is True
        assert config.smtp_host == "email-smtp.us-east-1.amazonaws.com"
        assert config.smtp_port == 587
        assert config.smtp_username == "AKIATESTUSER"
        assert config.smtp_password == "smtp-secret-pw"
        assert config.smtp_sender_email == "noreply@example.com"
        assert config.smtp_use_tls is True
        assert config.smtp_use_ssl is False
        # password is excluded from JSON serialization
        assert '"smtp_password"' not in config.model_dump_json()

    def test_full_flow_stripe_enabled(self, tmp_path):
        """When operator answers yes to Stripe, all 8 fields are collected and 4 secrets are excluded from JSON."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.select") as mock_select,
            patch("questionary.text") as mock_text,
        ):
            # passwords (4 stripe + GitHub + skip OpenAI + admin):
            mock_password.return_value.ask.side_effect = [
                "sk_test_secretvalue",
                "pk_test_pubvalue",
                "whsec_webhookvalue",
                "whsec_connectvalue",
                "ghp_testtoken",
                "",
                "Admin1234",
            ]
            # confirms: enable_ai, create_playwright_platforms, smtp_enabled,
            #          stripe_enabled, reuse_credentials
            mock_confirm.return_value.ask.side_effect = [
                True, False, False, True, True,
            ]
            # selects: stripe_mode
            mock_select.return_value.ask.return_value = "test"
            # texts: cli_ops_tag, pricing_table_id, pricing_table_id_returning, admin_username, admin_email
            mock_text.return_value.ask.side_effect = [
                "3.19.0",
                "prctbl_abcdef",
                "",
                "ibl_admin",
                "admin@example.com",
            ]

            config = prompt_setup(state)

        assert config.stripe_enabled is True
        assert config.stripe_mode == "test"
        assert config.stripe_secret_key == "sk_test_secretvalue"
        assert config.stripe_pub_key == "pk_test_pubvalue"
        assert config.stripe_pricing_table_id == "prctbl_abcdef"
        assert config.stripe_pricing_table_id_returning == ""
        assert config.stripe_webhook_secret == "whsec_webhookvalue"
        assert config.stripe_connect_webhook_secret == "whsec_connectvalue"
        # all 4 secret-shaped Stripe fields are excluded from JSON serialization
        dumped = config.model_dump_json()
        for excluded in ("stripe_secret_key", "stripe_pub_key", "stripe_webhook_secret", "stripe_connect_webhook_secret"):
            assert f'"{excluded}"' not in dumped


# ---------------------------------------------------------------------------
# prompt_resetup — questionary-mocked flow
# ---------------------------------------------------------------------------


class TestPromptResetup:
    def _make_state(self, tmp_path, ssh_method=SSHKeyMethod.GENERATE,
                    access_key_id="AKIA", secret_access_key="SECRET"):
        key_path = tmp_path / "key.pem"
        key_path.write_text("key-content")
        key_path.chmod(0o600)

        return ProjectState(
            name="test",
            config=InfraConfig(
                project_name="test",
                environment=Environment.DEV,
                credentials=AWSCredentials(
                    method=AuthMethod.ACCESS_KEY,
                    region="us-east-1",
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                ),
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(
                    method=ssh_method,
                    key_name="test-key",
                    private_key_path=key_path if ssh_method == SSHKeyMethod.GENERATE else None,
                ),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="old.example.com"),
            ),
            workspace_path=str(tmp_path),
            outputs={"instance_public_ip": "54.1.2.3"},
        )

    def test_full_resetup_flow(self, tmp_path):
        """Test the full resetup flow — prompts for new domain, no image tags."""
        from iblai_infra.prompts.setup import prompt_resetup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
            patch("iblai_infra.terraform.state.load_ingress", return_value=[]),
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            # Only one confirm: reuse credentials
            mock_confirm.return_value.ask.return_value = True
            # text prompts: base_domain, cli_ops_release_tag, admin_username, admin_email
            mock_text.return_value.ask.side_effect = ["new.example.com", "3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_resetup(state)

        assert config.is_resetup is True
        assert config.base_domain == "new.example.com"
        assert config.cli_ops_release_tag == "3.19.0"
        assert config.target_host == "54.1.2.3"
        assert config.aws_access_key_id == "AKIA"
        assert config.aws_secret_access_key == "SECRET"
        assert config.git_access_token == "ghp_testtoken"
        assert config.admin_username == "ibl_admin"
        assert config.admin_email == "admin@example.com"
        assert config.admin_password == "Admin1234"

    def test_resetup_prompts_for_base_domain(self, tmp_path):
        """Resetup always prompts for base domain even when state has one."""
        from iblai_infra.prompts.setup import prompt_resetup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
            patch("iblai_infra.terraform.state.load_ingress", return_value=[]),
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            mock_confirm.return_value.ask.return_value = True
            mock_text.return_value.ask.side_effect = ["changed.example.com", "3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_resetup(state)

        # Domain should be the new prompted value, not the state value
        assert config.base_domain == "changed.example.com"
        assert state.config.dns.base_domain == "old.example.com"

    def test_resetup_ssh_key_resolved(self, tmp_path):
        """SSH key from state is auto-resolved when available."""
        from iblai_infra.prompts.setup import prompt_resetup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
            patch("iblai_infra.terraform.state.load_ingress", return_value=[]),
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            mock_confirm.return_value.ask.return_value = True
            mock_text.return_value.ask.side_effect = ["new.example.com", "3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_resetup(state)

        assert config.ssh_private_key_path == tmp_path / "key.pem"

    def test_resetup_new_credentials(self, tmp_path):
        """Test resetup flow where user provides new AWS credentials."""
        from iblai_infra.prompts.setup import prompt_resetup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
            patch("iblai_infra.terraform.state.load_ingress", return_value=[]),
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "NEW_SECRET", "", "Admin1234"]
            # Decline reusing credentials
            mock_confirm.return_value.ask.return_value = False
            # Region is pre-populated from state, so not prompted
            mock_text.return_value.ask.side_effect = ["new.example.com", "3.19.0", "NEW_KEY", "ibl_admin", "admin@example.com"]

            config = prompt_resetup(state)

        assert config.aws_access_key_id == "NEW_KEY"
        assert config.aws_secret_access_key == "NEW_SECRET"
        assert config.aws_default_region == "us-east-1"  # from state

    def test_resetup_with_ingress_selection(self, tmp_path):
        """When ingress entries exist, resetup shows a select prompt."""
        from iblai_infra.prompts.setup import prompt_resetup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
            patch("questionary.select") as mock_select,
            patch("iblai_infra.terraform.state.load_ingress") as mock_load,
        ):
            from iblai_infra.models import IngressEntry
            mock_load.return_value = [
                IngressEntry(name="stg1", domain="stg1.example.com"),
                IngressEntry(name="stg2", domain="stg2.example.com"),
            ]
            # First select call is the ingress picker
            mock_select.return_value.ask.return_value = "stg2.example.com"
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            mock_confirm.return_value.ask.return_value = True
            # text prompts: cli_ops_release_tag, admin_username, admin_email
            mock_text.return_value.ask.side_effect = ["3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_resetup(state)

        assert config.base_domain == "stg2.example.com"
        assert config.is_resetup is True

    def test_resetup_ingress_custom_fallback(self, tmp_path):
        """Selecting 'Custom domain...' falls back to text input."""
        from iblai_infra.prompts.setup import prompt_resetup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
            patch("questionary.select") as mock_select,
            patch("iblai_infra.terraform.state.load_ingress") as mock_load,
        ):
            from iblai_infra.models import IngressEntry
            mock_load.return_value = [
                IngressEntry(name="stg1", domain="stg1.example.com"),
            ]
            mock_select.return_value.ask.return_value = "__custom__"
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "", "Admin1234"]
            mock_confirm.return_value.ask.return_value = True
            # text prompts: custom domain, cli_ops_release_tag, admin_username, admin_email
            mock_text.return_value.ask.side_effect = ["custom.example.com", "3.19.0", "ibl_admin", "admin@example.com"]

            config = prompt_resetup(state)

        assert config.base_domain == "custom.example.com"
