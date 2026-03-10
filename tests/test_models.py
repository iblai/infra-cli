"""Tests for iblai_infra.models — validation, edge cases, properties."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DNSConfig,
    Environment,
    IBL_SUBDOMAINS,
    InfraConfig,
    NetworkConfig,
    ProjectState,
    SSHConfig,
    SSHKeyMethod,
    SetupConfig,
    generate_password,
)


# ---------------------------------------------------------------------------
# InfraConfig.project_name validation
# ---------------------------------------------------------------------------


class TestProjectNameValidation:
    def test_valid_alphanumeric(self, infra_config):
        assert infra_config.project_name == "testproject"

    def test_valid_with_hyphens(self, aws_credentials):
        config = InfraConfig(
            project_name="my-project",
            environment=Environment.DEV,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert config.project_name == "my-project"

    def test_valid_with_underscores(self, aws_credentials):
        config = InfraConfig(
            project_name="my_project",
            environment=Environment.DEV,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert config.project_name == "my_project"

    def test_strips_whitespace_and_lowercases(self, aws_credentials):
        config = InfraConfig(
            project_name="  MyProject  ",
            environment=Environment.DEV,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert config.project_name == "myproject"

    def test_rejects_special_characters(self, aws_credentials):
        with pytest.raises(ValidationError, match="alphanumeric"):
            InfraConfig(
                project_name="my project!",
                environment=Environment.DEV,
                credentials=aws_credentials,
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            )

    def test_rejects_too_long(self, aws_credentials):
        with pytest.raises(ValidationError, match="32 characters"):
            InfraConfig(
                project_name="a" * 33,
                environment=Environment.DEV,
                credentials=aws_credentials,
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            )

    def test_max_length_accepted(self, aws_credentials):
        config = InfraConfig(
            project_name="a" * 32,
            environment=Environment.DEV,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert len(config.project_name) == 32


# ---------------------------------------------------------------------------
# NetworkConfig.vpn_ip validation
# ---------------------------------------------------------------------------


class TestNetworkConfigValidation:
    def test_valid_ipv4(self):
        nc = NetworkConfig(vpn_ip="192.168.1.1")
        assert nc.vpn_ip == "192.168.1.1"

    def test_valid_public_ip(self):
        nc = NetworkConfig(vpn_ip="203.0.113.42")
        assert nc.vpn_ip == "203.0.113.42"

    def test_invalid_ip_raises(self):
        with pytest.raises(ValidationError):
            NetworkConfig(vpn_ip="not-an-ip")

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            NetworkConfig(vpn_ip="")

    def test_ip_with_port_raises(self):
        with pytest.raises(ValidationError):
            NetworkConfig(vpn_ip="192.168.1.1:8080")

    def test_cidr_notation_raises(self):
        with pytest.raises(ValidationError):
            NetworkConfig(vpn_ip="10.0.0.0/16")

    def test_default_vpc_cidr(self):
        nc = NetworkConfig(vpn_ip="1.2.3.4")
        assert nc.vpc_cidr == "10.0.0.0/16"


# ---------------------------------------------------------------------------
# ComputeConfig.volume_size validation
# ---------------------------------------------------------------------------


class TestComputeConfigValidation:
    def test_valid_volume_size(self):
        cc = ComputeConfig(volume_size=100)
        assert cc.volume_size == 100

    def test_minimum_volume_size(self):
        cc = ComputeConfig(volume_size=20)
        assert cc.volume_size == 20

    def test_below_minimum_raises(self):
        with pytest.raises(ValidationError, match="at least 20 GB"):
            ComputeConfig(volume_size=19)

    def test_zero_raises(self):
        with pytest.raises(ValidationError, match="at least 20 GB"):
            ComputeConfig(volume_size=0)

    def test_negative_raises(self):
        with pytest.raises(ValidationError, match="at least 20 GB"):
            ComputeConfig(volume_size=-1)

    def test_defaults(self):
        cc = ComputeConfig()
        assert cc.instance_type == "t3.2xlarge"
        assert cc.volume_size == 50
        assert cc.volume_type == "gp3"


# ---------------------------------------------------------------------------
# DNSConfig.subdomains
# ---------------------------------------------------------------------------


class TestDNSConfig:
    def test_subdomains_generated(self):
        dns = DNSConfig(base_domain="example.com")
        subs = dns.subdomains
        assert len(subs) == len(IBL_SUBDOMAINS)
        assert "learn.example.com" in subs
        assert "studio.learn.example.com" in subs
        assert "api.data.example.com" in subs
        assert "monitor.example.com" in subs

    def test_subdomains_custom_domain(self):
        dns = DNSConfig(base_domain="ibl.education")
        subs = dns.subdomains
        assert "learn.ibl.education" in subs
        assert "flowise.ibl.education" in subs

    def test_default_no_route53(self):
        dns = DNSConfig(base_domain="example.com")
        assert dns.use_route53 is False
        assert dns.hosted_zone_id is None


# ---------------------------------------------------------------------------
# InfraConfig.resource_prefix
# ---------------------------------------------------------------------------


class TestInfraConfigProperties:
    def test_resource_prefix(self, infra_config):
        assert infra_config.resource_prefix == "testproject-dev"

    def test_resource_prefix_prod(self, aws_credentials):
        config = InfraConfig(
            project_name="myapp",
            environment=Environment.PROD,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert config.resource_prefix == "myapp-prod"


# ---------------------------------------------------------------------------
# ProjectState
# ---------------------------------------------------------------------------


class TestProjectState:
    def test_defaults(self, infra_config):
        state = ProjectState(name="test", config=infra_config)
        assert state.status == "initialized"
        assert state.provider == "aws"
        assert state.outputs is None
        assert state.setup_status is None
        assert state.setup_completed_at is None

    def test_serialization_roundtrip(self, project_state):
        json_str = project_state.model_dump_json()
        loaded = ProjectState.model_validate_json(json_str)
        assert loaded.name == project_state.name
        assert loaded.status == project_state.status
        assert loaded.config.project_name == "testproject"
        assert loaded.outputs["instance_public_ip"] == "54.123.45.67"

    def test_status_literals(self, infra_config):
        for status in ("initialized", "created", "failed", "destroyed"):
            state = ProjectState(name="test", config=infra_config, status=status)
            assert state.status == status

    def test_invalid_status_rejected(self, infra_config):
        with pytest.raises(ValidationError):
            ProjectState(name="test", config=infra_config, status="invalid")

    def test_setup_status_literals(self, infra_config):
        for status in ("pending", "running", "completed", "failed"):
            state = ProjectState(name="test", config=infra_config, setup_status=status)
            assert state.setup_status == status


# ---------------------------------------------------------------------------
# SetupConfig
# ---------------------------------------------------------------------------


class TestSetupConfig:
    def test_valid(self, setup_config):
        assert setup_config.ssh_user == "ubuntu"
        assert setup_config.edx_version == "sumac"
        assert setup_config.env_config == "single-server"

    def test_defaults(self, tmp_path):
        key = tmp_path / "k.pem"
        key.touch()
        sc = SetupConfig(
            ssh_private_key_path=key,
            target_host="1.2.3.4",
            base_domain="example.com",
            git_access_token="ghp_abc",
            aws_access_key_id="AK",
            aws_secret_access_key="SK",
            aws_default_region="us-east-1",
        )
        assert sc.ssh_user == "ubuntu"
        assert sc.edx_version == "sumac"
        assert sc.env_config == "single-server"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_auth_methods(self):
        assert AuthMethod.PROFILE.value == "profile"
        assert AuthMethod.ACCESS_KEY.value == "access_key"
        assert AuthMethod.ENVIRONMENT.value == "environment"

    def test_ssh_key_methods(self):
        assert SSHKeyMethod.GENERATE.value == "generate"
        assert SSHKeyMethod.EXISTING_FILE.value == "existing_file"
        assert SSHKeyMethod.AWS_KEYPAIR.value == "aws_keypair"

    def test_cert_methods(self):
        assert CertMethod.ACM.value == "acm"
        assert CertMethod.UPLOAD.value == "upload"
        assert CertMethod.NONE.value == "none"

    def test_environments(self):
        assert Environment.DEV.value == "dev"
        assert Environment.STAGING.value == "staging"
        assert Environment.PROD.value == "prod"


# ---------------------------------------------------------------------------
# generate_password
# ---------------------------------------------------------------------------


class TestGeneratePassword:
    def test_default_length(self):
        pw = generate_password()
        assert len(pw) == 24

    def test_custom_length(self):
        pw = generate_password(length=48)
        assert len(pw) == 48

    def test_alphanumeric_only(self):
        pw = generate_password(length=1000)
        assert pw.isalnum()

    def test_uniqueness(self):
        passwords = {generate_password() for _ in range(50)}
        assert len(passwords) == 50


# ---------------------------------------------------------------------------
# Additional model edge cases
# ---------------------------------------------------------------------------


class TestProjectNameEdgeCases:
    def test_only_hyphens(self, aws_credentials):
        """Hyphens alone fail because removing them leaves empty string."""
        with pytest.raises(ValidationError, match="alphanumeric"):
            InfraConfig(
                project_name="---",
                environment=Environment.DEV,
                credentials=aws_credentials,
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            )

    def test_dots_rejected(self, aws_credentials):
        with pytest.raises(ValidationError, match="alphanumeric"):
            InfraConfig(
                project_name="my.project",
                environment=Environment.DEV,
                credentials=aws_credentials,
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            )

    def test_spaces_rejected(self, aws_credentials):
        with pytest.raises(ValidationError, match="alphanumeric"):
            InfraConfig(
                project_name="my project",
                environment=Environment.DEV,
                credentials=aws_credentials,
                network=NetworkConfig(vpn_ip="1.2.3.4"),
                compute=ComputeConfig(),
                ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            )

    def test_single_char(self, aws_credentials):
        config = InfraConfig(
            project_name="a",
            environment=Environment.DEV,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert config.project_name == "a"

    def test_mixed_case_normalized(self, aws_credentials):
        config = InfraConfig(
            project_name="MyApp-Test_123",
            environment=Environment.DEV,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert config.project_name == "myapp-test_123"


class TestAWSCredentialsCombinations:
    def test_profile_only(self):
        creds = AWSCredentials(
            method=AuthMethod.PROFILE,
            profile="myprofile",
            region="us-east-1",
        )
        assert creds.access_key_id is None
        assert creds.secret_access_key is None

    def test_access_key_only(self):
        creds = AWSCredentials(
            method=AuthMethod.ACCESS_KEY,
            access_key_id="AKIA",
            secret_access_key="secret",
            region="us-east-1",
        )
        assert creds.profile is None

    def test_environment_only(self):
        creds = AWSCredentials(
            method=AuthMethod.ENVIRONMENT,
            region="us-east-1",
        )
        assert creds.profile is None
        assert creds.access_key_id is None

    def test_all_regions_valid(self):
        from iblai_infra.models import AWS_REGIONS
        for region in AWS_REGIONS:
            creds = AWSCredentials(method=AuthMethod.ENVIRONMENT, region=region)
            assert creds.region == region


class TestCertificateConfigCombinations:
    def test_acm_with_zone(self):
        cc = CertificateConfig(method=CertMethod.ACM, hosted_zone_id="Z12345")
        assert cc.cert_body is None
        assert cc.cert_private_key is None

    def test_upload_full(self):
        cc = CertificateConfig(
            method=CertMethod.UPLOAD,
            cert_body="-----BEGIN CERT-----",
            cert_private_key="-----BEGIN KEY-----",
            cert_chain="-----BEGIN CHAIN-----",
        )
        assert cc.hosted_zone_id is None

    def test_upload_without_chain(self):
        cc = CertificateConfig(
            method=CertMethod.UPLOAD,
            cert_body="cert",
            cert_private_key="key",
        )
        assert cc.cert_chain is None

    def test_none_method(self):
        cc = CertificateConfig(method=CertMethod.NONE)
        assert cc.cert_body is None
        assert cc.hosted_zone_id is None


class TestSSHConfigCombinations:
    def test_generate_with_all_fields(self, tmp_path):
        key = tmp_path / "key.pem"
        key.touch()
        sc = SSHConfig(
            method=SSHKeyMethod.GENERATE,
            key_name="test-key",
            public_key="ssh-ed25519 AAAA...",
            private_key_path=key,
        )
        assert sc.public_key is not None
        assert sc.private_key_path is not None

    def test_existing_file_with_public_key(self):
        sc = SSHConfig(
            method=SSHKeyMethod.EXISTING_FILE,
            key_name="my-key",
            public_key="ssh-rsa AAAA...",
        )
        assert sc.private_key_path is None

    def test_aws_keypair_name_only(self):
        sc = SSHConfig(
            method=SSHKeyMethod.AWS_KEYPAIR,
            key_name="aws-key-name",
        )
        assert sc.public_key is None
        assert sc.private_key_path is None


class TestResourcePrefixAllEnvironments:
    @pytest.mark.parametrize("env", list(Environment))
    def test_all_environments(self, aws_credentials, env):
        config = InfraConfig(
            project_name="myproject",
            environment=env,
            credentials=aws_credentials,
            network=NetworkConfig(vpn_ip="1.2.3.4"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert config.resource_prefix == f"myproject-{env.value}"


class TestNetworkIPEdgeCases:
    def test_ipv6_address(self):
        nc = NetworkConfig(vpn_ip="::1")
        assert nc.vpn_ip == "::1"

    def test_full_ipv6(self):
        nc = NetworkConfig(vpn_ip="2001:db8::1")
        assert nc.vpn_ip == "2001:db8::1"

    def test_broadcast_address(self):
        nc = NetworkConfig(vpn_ip="255.255.255.255")
        assert nc.vpn_ip == "255.255.255.255"

    def test_zero_address(self):
        nc = NetworkConfig(vpn_ip="0.0.0.0")
        assert nc.vpn_ip == "0.0.0.0"
