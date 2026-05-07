"""Tests for iblai_infra.app — wizard orchestrator edge cases."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iblai_infra.app import show_results, show_workspace, _offer_setup
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


def _make_config(**kwargs) -> InfraConfig:
    defaults = dict(
        project_name="test",
        environment=Environment.DEV,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY, region="us-east-1",
            access_key_id="AK", secret_access_key="SK",
        ),
        network=NetworkConfig(vpn_ip="1.2.3.4"),
        compute=ComputeConfig(),
        ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="test"),
        certificates=CertificateConfig(method=CertMethod.NONE),
        dns=DNSConfig(base_domain="example.com"),
    )
    defaults.update(kwargs)
    return InfraConfig(**defaults)


# ---------------------------------------------------------------------------
# show_workspace
# ---------------------------------------------------------------------------


class TestShowWorkspace:
    def test_existing_workspace(self, tmp_path):
        (tmp_path / "main.tf").write_text("resource {}")
        (tmp_path / "terraform.tfvars").write_text("foo = bar")
        show_workspace(tmp_path)  # Should not raise

    def test_empty_workspace(self, tmp_path):
        ws = tmp_path / "empty"
        ws.mkdir()
        show_workspace(ws)  # No files, returns early

    def test_nonexistent_workspace(self, tmp_path):
        ws = tmp_path / "nonexistent"
        show_workspace(ws)  # Should not raise

    def test_file_sizes_bytes(self, tmp_path):
        (tmp_path / "small.tf").write_text("x")
        show_workspace(tmp_path)

    def test_file_sizes_kb(self, tmp_path):
        (tmp_path / "large.tf").write_text("x" * 2048)
        show_workspace(tmp_path)


# ---------------------------------------------------------------------------
# show_results — all output field combinations
# ---------------------------------------------------------------------------


class TestShowResults:
    def test_all_outputs(self, tmp_path):
        config = _make_config(ssh=SSHConfig(
            method=SSHKeyMethod.GENERATE, key_name="k",
            private_key_path=tmp_path / "key.pem",
        ))
        outputs = {
            "instance_public_ip": "1.2.3.4",
            "instance_private_ip": "10.0.0.5",
            "alb_dns_name": "alb.example.com",
            "s3_bucket_backups": "test-backups",
            "s3_bucket_media": "test-media",
            "s3_bucket_static": "test-static",
            "application_url": "https://example.com",
        }
        show_results(config, outputs, tmp_path)

    def test_minimal_outputs(self, tmp_path):
        config = _make_config()
        outputs = {"instance_public_ip": "1.2.3.4"}
        show_results(config, outputs, tmp_path)

    def test_empty_outputs(self, tmp_path):
        config = _make_config()
        show_results(config, {}, tmp_path)

    def test_ssh_command_in_outputs(self, tmp_path):
        config = _make_config()
        outputs = {
            "instance_public_ip": "1.2.3.4",
            "ssh_command": "ssh -i key.pem ubuntu@1.2.3.4",
        }
        show_results(config, outputs, tmp_path)

    def test_ssh_command_generated_with_key(self, tmp_path):
        config = _make_config(ssh=SSHConfig(
            method=SSHKeyMethod.GENERATE, key_name="k",
            private_key_path=Path("/home/user/.ssh/key.pem"),
        ))
        outputs = {"instance_public_ip": "1.2.3.4"}
        show_results(config, outputs, tmp_path)

    def test_ssh_command_generated_without_key(self, tmp_path):
        config = _make_config()
        outputs = {"instance_public_ip": "1.2.3.4"}
        show_results(config, outputs, tmp_path)


# ---------------------------------------------------------------------------
# _offer_setup — all paths
# ---------------------------------------------------------------------------


class TestOfferSetup:
    def _make_state(self, tmp_path) -> ProjectState:
        return ProjectState(
            name="test",
            config=_make_config(),
            workspace_path=str(tmp_path),
            outputs={"instance_public_ip": "1.2.3.4"},
        )

    def test_not_project_state(self):
        """Non-ProjectState object should return immediately."""
        config = _make_config()
        _offer_setup(config, "not a state")  # Should not raise

    def test_ansible_not_installed(self, tmp_path):
        state = self._make_state(tmp_path)
        config = _make_config()
        with patch("shutil.which", return_value=None):
            _offer_setup(config, state)  # Should print message and return

    def test_user_declines_setup(self, tmp_path):
        state = self._make_state(tmp_path)
        config = _make_config()
        with (
            patch("shutil.which", return_value="/usr/bin/ansible-playbook"),
            patch("questionary.confirm") as mock_confirm,
        ):
            mock_confirm.return_value.ask.return_value = False
            _offer_setup(config, state)  # Should return with message

    def test_user_accepts_but_keyboard_interrupt(self, tmp_path):
        state = self._make_state(tmp_path)
        config = _make_config()
        with (
            patch("shutil.which", return_value="/usr/bin/ansible-playbook"),
            patch("questionary.confirm") as mock_confirm,
            patch("iblai_infra.prompts.setup.prompt_setup", side_effect=KeyboardInterrupt),
        ):
            mock_confirm.return_value.ask.return_value = True
            _offer_setup(config, state)  # Should handle gracefully

    def test_user_accepts_preflight_fails(self, tmp_path):
        state = self._make_state(tmp_path)
        config = _make_config()
        mock_runner = MagicMock()
        mock_runner.preflight.return_value = False

        with (
            patch("shutil.which", return_value="/usr/bin/ansible-playbook"),
            patch("questionary.confirm") as mock_confirm,
            patch("iblai_infra.prompts.setup.prompt_setup") as mock_prompt,
            patch("iblai_infra.ansible.runner.AnsibleRunner", return_value=mock_runner),
        ):
            mock_confirm.return_value.ask.return_value = True
            mock_prompt.return_value = MagicMock()
            _offer_setup(config, state)
