"""Tests for iblai_infra.env_provision.build_infra_config_from_env.

Covers the .env → InfraConfig pipeline used by `iblai infra provision-env`.
Everything is mocked — no live AWS / no boto3 / no real subprocess calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from iblai_infra.env_provision import build_infra_config_from_env
from iblai_infra.models import (
    AuthMethod,
    CertMethod,
    DeploymentType,
    Environment,
    SSHKeyMethod,
)
from iblai_infra.providers.aws import HostedZone, KeyPairInfo


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

def _minimal_env(**overrides) -> dict[str, str]:
    """Smallest .env that should produce a valid InfraConfig."""
    base = {
        "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "PROJECT_NAME": "testproj",
        "DOMAIN": "example.com",
        "VPN_IP": "203.0.113.7",
        "SSH_KEY_METHOD": "aws_keypair",
        "SSH_KEY_NAME": "existing-key",
        "CERT_METHOD": "none",
    }
    base.update(overrides)
    return base


def _identity():
    return MagicMock(account_id="123456789012", arn="arn:aws:iam::123456789012:user/t")


@pytest.fixture(autouse=True)
def _no_session_persistence(monkeypatch):
    """save_session writes to ~/.iblai-infra/session.json. Stub it so
    tests don't touch the operator's real home dir."""
    monkeypatch.setattr("iblai_infra.terraform.state.save_session", lambda creds: None)
    yield


@pytest.fixture
def patched_aws():
    """Patches every AWS helper env_provision touches.

    Returns a SimpleNamespace-style object so tests can override individual
    behaviours via .return_value etc.
    """
    with patch("iblai_infra.env_provision.validate_credentials", return_value=_identity()) as v, \
         patch("iblai_infra.env_provision.get_session", return_value=MagicMock()) as gs, \
         patch("iblai_infra.env_provision.list_hosted_zones", return_value=[]) as lhz, \
         patch("iblai_infra.env_provision.list_key_pairs", return_value=[
             KeyPairInfo(name="existing-key", key_id="key-1", key_type="ed25519")
         ]) as lkp, \
         patch("iblai_infra.env_provision.find_conflicting_records", return_value=[]) as fcr, \
         patch("iblai_infra.env_provision.delete_route53_records") as drr, \
         patch("iblai_infra.env_provision.detect_current_ip", return_value="198.51.100.1") as dip:
        yield {
            "validate_credentials": v,
            "get_session": gs,
            "list_hosted_zones": lhz,
            "list_key_pairs": lkp,
            "find_conflicting_records": fcr,
            "delete_route53_records": drr,
            "detect_current_ip": dip,
        }


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

class TestMinimalConfig:
    def test_minimal_env_produces_valid_config(self, patched_aws):
        config = build_infra_config_from_env(_minimal_env())
        assert config.project_name == "testproj"
        assert config.environment == Environment.STAGING
        assert config.deployment_type == DeploymentType.SINGLE
        assert config.network.vpn_ip == "203.0.113.7"
        assert config.compute.instance_type == "t3.2xlarge"
        assert config.dns.base_domain == "example.com"
        assert config.certificates.method == CertMethod.NONE
        assert config.ssh.method == SSHKeyMethod.AWS_KEYPAIR
        assert config.ssh.key_name == "existing-key"

    def test_credentials_via_access_keys(self, patched_aws):
        config = build_infra_config_from_env(_minimal_env())
        assert config.credentials.method == AuthMethod.ACCESS_KEY
        assert config.credentials.access_key_id == "AKIAIOSFODNN7EXAMPLE"

    def test_credentials_via_profile(self, patched_aws):
        env = _minimal_env(
            AWS_ACCESS_KEY_ID="",
            AWS_SECRET_ACCESS_KEY="",
            AWS_PROFILE="myprofile",
        )
        config = build_infra_config_from_env(env)
        assert config.credentials.method == AuthMethod.PROFILE
        assert config.credentials.profile == "myprofile"

    def test_environment_override(self, patched_aws):
        config = build_infra_config_from_env(_minimal_env(ENVIRONMENT="prod"))
        assert config.environment == Environment.PROD

    def test_compute_overrides(self, patched_aws):
        env = _minimal_env(
            INSTANCE_TYPE="r5.2xlarge",
            VOLUME_SIZE="200",
            VOLUME_TYPE="gp2",
        )
        config = build_infra_config_from_env(env)
        assert config.compute.instance_type == "r5.2xlarge"
        assert config.compute.volume_size == 200
        assert config.compute.volume_type == "gp2"

    def test_vpc_cidr_override(self, patched_aws):
        config = build_infra_config_from_env(_minimal_env(VPC_CIDR="10.42.0.0/16"))
        assert config.network.vpc_cidr == "10.42.0.0/16"


# ---------------------------------------------------------------------------
# Required-key validation
# ---------------------------------------------------------------------------

class TestMissingRequired:
    def test_no_aws_creds(self):
        env = _minimal_env(AWS_ACCESS_KEY_ID="", AWS_SECRET_ACCESS_KEY="")
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_missing_project_name(self, patched_aws):
        env = _minimal_env()
        del env["PROJECT_NAME"]
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_missing_domain(self, patched_aws):
        env = _minimal_env()
        del env["DOMAIN"]
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_missing_vpn_ip(self, patched_aws):
        env = _minimal_env()
        del env["VPN_IP"]
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_invalid_environment(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(ENVIRONMENT="qa"))

    def test_invalid_vpn_ip(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(VPN_IP="not-an-ip"))

    def test_volume_size_below_minimum(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(VOLUME_SIZE="10"))

    def test_volume_size_not_integer(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(VOLUME_SIZE="big"))

    def test_unsupported_deployment_type(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(
                _minimal_env(DEPLOYMENT_TYPE="multi-server")
            )

    def test_invalid_cert_method(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(CERT_METHOD="weird"))

    def test_invalid_ssh_method(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(SSH_KEY_METHOD="elsewhere"))

    def test_aws_creds_invalid(self, patched_aws):
        patched_aws["validate_credentials"].side_effect = ValueError("bad keys")
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env())


# ---------------------------------------------------------------------------
# VPN IP auto-detect
# ---------------------------------------------------------------------------

class TestVpnIpAuto:
    def test_auto_detected(self, patched_aws):
        config = build_infra_config_from_env(_minimal_env(VPN_IP="auto"))
        assert config.network.vpn_ip == "198.51.100.1"
        patched_aws["detect_current_ip"].assert_called_once()

    def test_auto_detect_returns_none(self, patched_aws):
        patched_aws["detect_current_ip"].return_value = None
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(VPN_IP="auto"))


# ---------------------------------------------------------------------------
# SSH key methods
# ---------------------------------------------------------------------------

class TestSshKeyMethods:
    def test_generate_calls_keypair_helper(self, patched_aws, tmp_path):
        with patch("iblai_infra.env_provision.generate_keypair") as gk:
            gk.return_value = (tmp_path / "key", "ssh-ed25519 AAAA...")
            env = _minimal_env(SSH_KEY_METHOD="generate")
            del env["SSH_KEY_NAME"]
            config = build_infra_config_from_env(env)
        assert config.ssh.method == SSHKeyMethod.GENERATE
        assert config.ssh.public_key == "ssh-ed25519 AAAA..."
        gk.assert_called_once_with("testproj-staging")

    def test_existing_file_from_path(self, patched_aws, tmp_path):
        pub = tmp_path / "id_test.pub"
        pub.write_text("ssh-rsa FROMFILE me@host\n")
        env = _minimal_env(
            SSH_KEY_METHOD="existing_file",
            SSH_PUBLIC_KEY_PATH=str(pub),
        )
        del env["SSH_KEY_NAME"]
        config = build_infra_config_from_env(env)
        assert config.ssh.public_key == "ssh-rsa FROMFILE me@host"
        assert config.ssh.key_name == "id_test"

    def test_existing_file_inline(self, patched_aws):
        env = _minimal_env(
            SSH_KEY_METHOD="existing_file",
            SSH_PUBLIC_KEY="ssh-ed25519 INLINE user@host",
        )
        del env["SSH_KEY_NAME"]
        config = build_infra_config_from_env(env)
        assert config.ssh.public_key == "ssh-ed25519 INLINE user@host"

    def test_existing_file_path_missing(self, patched_aws, tmp_path):
        env = _minimal_env(
            SSH_KEY_METHOD="existing_file",
            SSH_PUBLIC_KEY_PATH=str(tmp_path / "nope.pub"),
        )
        del env["SSH_KEY_NAME"]
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_existing_file_no_source(self, patched_aws):
        env = _minimal_env(SSH_KEY_METHOD="existing_file")
        del env["SSH_KEY_NAME"]
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_aws_keypair_unknown_name(self, patched_aws):
        patched_aws["list_key_pairs"].return_value = []
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env())

    def test_aws_keypair_missing_name(self, patched_aws):
        env = _minimal_env()
        del env["SSH_KEY_NAME"]
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)


# ---------------------------------------------------------------------------
# Cert resolution
# ---------------------------------------------------------------------------

class TestCertResolution:
    def test_auto_with_matching_zone_uses_acm(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = [
            HostedZone(zone_id="Z123", name="example.com", record_count=2, private=False)
        ]
        config = build_infra_config_from_env(_minimal_env(CERT_METHOD="auto"))
        assert config.certificates.method == CertMethod.ACM
        assert config.certificates.hosted_zone_id == "Z123"
        assert config.dns.use_route53 is True

    def test_auto_no_match_falls_back_to_none(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = []
        config = build_infra_config_from_env(_minimal_env(CERT_METHOD="auto"))
        assert config.certificates.method == CertMethod.NONE
        assert config.dns.use_route53 is False

    def test_acm_no_matching_zone_errors(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = []
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(CERT_METHOD="acm"))

    def test_acm_multiple_zones_requires_disambiguation(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = [
            HostedZone(zone_id="Z1", name="example.com", record_count=1, private=False),
            HostedZone(zone_id="Z2", name="example.com", record_count=1, private=False),
        ]
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(CERT_METHOD="acm"))

    def test_acm_explicit_hosted_zone_id_resolves(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = [
            HostedZone(zone_id="Z1", name="example.com", record_count=1, private=False),
            HostedZone(zone_id="Z2", name="example.com", record_count=1, private=False),
        ]
        config = build_infra_config_from_env(
            _minimal_env(CERT_METHOD="acm", HOSTED_ZONE_ID="Z2")
        )
        assert config.certificates.hosted_zone_id == "Z2"

    def test_upload_loads_pem_files(self, patched_aws, tmp_path):
        body = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        body.write_text("-----BEGIN CERTIFICATE-----\nbody\n-----END CERTIFICATE-----\n")
        key.write_text("-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n")
        env = _minimal_env(
            CERT_METHOD="upload",
            CERT_BODY_PATH=str(body),
            CERT_KEY_PATH=str(key),
        )
        config = build_infra_config_from_env(env)
        assert config.certificates.method == CertMethod.UPLOAD
        assert "BEGIN CERTIFICATE" in config.certificates.cert_body
        assert "BEGIN PRIVATE KEY" in config.certificates.cert_private_key

    def test_upload_missing_paths_errors(self, patched_aws):
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(_minimal_env(CERT_METHOD="upload"))


# ---------------------------------------------------------------------------
# DNS conflict handling
# ---------------------------------------------------------------------------

class TestDnsConflicts:
    def test_auto_delete_default_true(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = [
            HostedZone(zone_id="Z123", name="example.com", record_count=1, private=False)
        ]
        patched_aws["find_conflicting_records"].return_value = [
            {"Name": "learn.example.com.", "Type": "CNAME", "ResourceRecords": [{"Value": "old.example.com."}]}
        ]
        build_infra_config_from_env(_minimal_env(CERT_METHOD="acm"))
        patched_aws["delete_route53_records"].assert_called_once()

    def test_auto_delete_false_errors_with_conflicts(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = [
            HostedZone(zone_id="Z123", name="example.com", record_count=1, private=False)
        ]
        patched_aws["find_conflicting_records"].return_value = [
            {"Name": "learn.example.com.", "Type": "CNAME", "ResourceRecords": [{"Value": "old.example.com."}]}
        ]
        env = _minimal_env(
            CERT_METHOD="acm",
            AUTO_DELETE_CONFLICTING_DNS="false",
        )
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)
        patched_aws["delete_route53_records"].assert_not_called()

    def test_no_conflicts_skips_delete(self, patched_aws):
        patched_aws["list_hosted_zones"].return_value = [
            HostedZone(zone_id="Z123", name="example.com", record_count=1, private=False)
        ]
        patched_aws["find_conflicting_records"].return_value = []
        build_infra_config_from_env(_minimal_env(CERT_METHOD="acm"))
        patched_aws["delete_route53_records"].assert_not_called()


# ---------------------------------------------------------------------------
# WAF (.env-driven)
# ---------------------------------------------------------------------------

class TestWafFromEnv:
    def test_waf_default_disabled(self, patched_aws):
        config = build_infra_config_from_env(_minimal_env())
        # WAFConfig is always built; enabled defaults to False
        assert config.waf is not None
        assert config.waf.enabled is False
        assert config.waf.allowed_ips == []

    def test_waf_enabled_with_bare_ips(self, patched_aws):
        env = _minimal_env(
            ENABLE_WAF="true",
            WAF_ALLOWED_IPS="203.0.113.7,198.51.100.0/24",
        )
        config = build_infra_config_from_env(env)
        assert config.waf.enabled is True
        assert config.waf.allowed_ips == ["203.0.113.7/32", "198.51.100.0/24"]

    def test_waf_enabled_without_ips_errors(self, patched_aws):
        env = _minimal_env(ENABLE_WAF="true", WAF_ALLOWED_IPS="")
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_waf_enabled_with_only_whitespace_errors(self, patched_aws):
        env = _minimal_env(ENABLE_WAF="true", WAF_ALLOWED_IPS=" , ,  ")
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_waf_enabled_invalid_token_errors(self, patched_aws):
        env = _minimal_env(ENABLE_WAF="true", WAF_ALLOWED_IPS="203.0.113.7,not-an-ip")
        with pytest.raises(typer.Exit):
            build_infra_config_from_env(env)

    def test_waf_disabled_ignores_ips(self, patched_aws):
        # Operator left ENABLE_WAF unset but accidentally populated the list —
        # WAFConfig accepts and normalises but stays disabled. The Terraform
        # runner only emits enable_waf=true when the flag is on.
        env = _minimal_env(WAF_ALLOWED_IPS="203.0.113.7")
        config = build_infra_config_from_env(env)
        assert config.waf.enabled is False
        # Normalisation still runs (cheap and lets us reuse the same model)
        assert config.waf.allowed_ips == ["203.0.113.7/32"]
