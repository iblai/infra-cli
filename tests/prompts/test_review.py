"""Tests for iblai_infra.prompts.review — summary display for all config combos."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
    SSHConfig,
    SSHKeyMethod,
)
from iblai_infra.prompts.review import prompt_review


def _make_config(
    ssh_method=SSHKeyMethod.GENERATE,
    cert_method=CertMethod.NONE,
    environment=Environment.DEV,
    private_key_path=None,
    hosted_zone_id=None,
) -> InfraConfig:
    return InfraConfig(
        project_name="test",
        environment=environment,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY,
            region="us-east-1",
            access_key_id="AK",
            secret_access_key="SK",
            account_id="123456789012",
        ),
        network=NetworkConfig(vpn_ip="1.2.3.4"),
        compute=ComputeConfig(instance_type="t3.2xlarge", volume_size=50, volume_type="gp3"),
        ssh=SSHConfig(
            method=ssh_method,
            key_name="test-key",
            private_key_path=private_key_path,
        ),
        certificates=CertificateConfig(
            method=cert_method,
            hosted_zone_id=hosted_zone_id,
        ),
        dns=DNSConfig(base_domain="example.com", use_route53=(cert_method == CertMethod.ACM)),
    )


class TestPromptReview:
    def test_confirm_proceeds(self):
        config = _make_config()
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            result = prompt_review(config)
            assert result is True

    def test_decline_aborts(self):
        config = _make_config()
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = False
            with pytest.raises(SystemExit):
                prompt_review(config)

    def test_cancel_aborts(self):
        config = _make_config()
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = None
            with pytest.raises(SystemExit):
                prompt_review(config)

    # ----- SSH method display branches -----

    def test_ssh_generate_with_key_path(self, tmp_path):
        key = tmp_path / "key.pem"
        key.touch()
        config = _make_config(ssh_method=SSHKeyMethod.GENERATE, private_key_path=key)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    def test_ssh_generate_without_key_path(self):
        config = _make_config(ssh_method=SSHKeyMethod.GENERATE)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    def test_ssh_existing_file(self):
        config = _make_config(ssh_method=SSHKeyMethod.EXISTING_FILE)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    def test_ssh_aws_keypair(self):
        config = _make_config(ssh_method=SSHKeyMethod.AWS_KEYPAIR)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    # ----- Certificate method display branches -----

    def test_cert_acm(self):
        config = _make_config(cert_method=CertMethod.ACM, hosted_zone_id="Z12345")
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    def test_cert_upload(self):
        config = _make_config(cert_method=CertMethod.UPLOAD)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    def test_cert_none(self):
        config = _make_config(cert_method=CertMethod.NONE)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    # ----- All SSH × cert combinations -----

    @pytest.mark.parametrize("ssh_method", list(SSHKeyMethod))
    @pytest.mark.parametrize("cert_method", list(CertMethod))
    def test_all_ssh_cert_combinations(self, ssh_method, cert_method):
        hosted_zone = "Z12345" if cert_method == CertMethod.ACM else None
        config = _make_config(ssh_method=ssh_method, cert_method=cert_method, hosted_zone_id=hosted_zone)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            result = prompt_review(config)
            assert result is True

    # ----- All environments -----

    @pytest.mark.parametrize("env", list(Environment))
    def test_all_environments(self, env):
        config = _make_config(environment=env)
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)

    # ----- Account ID None -----

    def test_no_account_id(self):
        config = _make_config()
        config.credentials.account_id = None
        with patch("questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = True
            prompt_review(config)
