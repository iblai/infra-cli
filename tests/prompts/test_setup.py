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
            mock_password.return_value.ask.return_value = "ghp_testtoken"
            # First confirm: enable AI, second confirm: reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, True]
            mock_text.return_value.ask.side_effect = ["4.189.1-ai", "sumac.2.4.13", "1.13.15", "0.35.14", "0.9.8"]

            config = prompt_setup(state)

        assert config.edx_version == "sumac"
        assert config.env_config == "single-server"
        assert config.dm_image_tag == "4.189.1-ai"
        assert config.edx_image_tag == "sumac.2.4.13"
        assert config.enable_ai is True
        assert config.spa_auth_image_tag == "1.13.15"
        assert config.spa_mentor_image_tag == "0.35.14"
        assert config.spa_skills_image_tag == "0.9.8"
        assert config.aws_access_key_id == "AKIA"
        assert config.aws_secret_access_key == "SECRET"
        assert config.git_access_token == "ghp_testtoken"
        assert config.target_host == "54.1.2.3"
        assert config.base_domain == "example.com"

    def test_full_flow_new_credentials(self, tmp_path):
        """Test the flow where user declines reusing credentials."""
        from iblai_infra.prompts.setup import prompt_setup

        state = self._make_state(tmp_path)

        with (
            patch("questionary.password") as mock_password,
            patch("questionary.confirm") as mock_confirm,
            patch("questionary.text") as mock_text,
        ):
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "NEW_SECRET", "sk-test-key"]
            # First confirm: enable AI, second confirm: don't reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, False]
            mock_text.return_value.ask.side_effect = ["4.189.1-ai", "sumac.2.4.13", "1.13.15", "0.35.14", "0.9.8", "NEW_ACCESS_KEY"]

            config = prompt_setup(state)

        assert config.edx_version == "sumac"
        assert config.env_config == "single-server"
        assert config.dm_image_tag == "4.189.1-ai"
        assert config.edx_image_tag == "sumac.2.4.13"
        assert config.enable_ai is True
        assert config.spa_auth_image_tag == "1.13.15"
        assert config.spa_mentor_image_tag == "0.35.14"
        assert config.spa_skills_image_tag == "0.9.8"
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
            mock_password.return_value.ask.side_effect = ["ghp_testtoken", "SECRET", ""]
            # Only one confirm: enable AI (no reuse prompt when no access keys)
            mock_confirm.return_value.ask.return_value = True
            mock_text.return_value.ask.side_effect = ["4.189.1-ai", "sumac.2.4.13", "1.13.15", "0.35.14", "0.9.8", "ACCESS_KEY"]

            config = prompt_setup(state)

        assert config.edx_version == "sumac"
        assert config.env_config == "single-server"
        assert config.dm_image_tag == "4.189.1-ai"
        assert config.edx_image_tag == "sumac.2.4.13"
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
            mock_password.return_value.ask.return_value = "ghp_testtoken"
            # First confirm: enable AI, second confirm: reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, True]
            mock_path.return_value.ask.return_value = str(new_key)
            mock_text.return_value.ask.side_effect = ["4.189.1-ai", "sumac.2.4.13", "1.13.15", "0.35.14", "0.9.8"]

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
            mock_password.return_value.ask.return_value = "ghp_testtoken"
            # First confirm: enable AI, second confirm: reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, True]
            mock_path.return_value.ask.return_value = str(key)
            mock_text.return_value.ask.side_effect = ["4.189.1-ai", "sumac.2.4.13", "1.13.15", "0.35.14", "0.9.8"]

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
            mock_password.return_value.ask.return_value = "ghp_testtoken"
            # First confirm: enable AI, second confirm: reuse credentials
            mock_confirm.return_value.ask.side_effect = [True, True]
            mock_path.return_value.ask.return_value = str(key)
            mock_text.return_value.ask.side_effect = ["4.189.1-ai", "sumac.2.4.13", "1.13.15", "0.35.14", "0.9.8"]

            config = prompt_setup(state)

        assert config.ssh_private_key_path == key
