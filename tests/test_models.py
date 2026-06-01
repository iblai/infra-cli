"""Tests for iblai_infra.models — validation, edge cases, properties."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CallServerConfig,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DeploymentType,
    DNSConfig,
    Environment,
    IBL_SUBDOMAINS,
    InfraConfig,
    IngressEntry,
    IngressLockConfig,
    IngressRegistry,
    NetworkConfig,
    ProjectState,
    SSHConfig,
    SSHKeyMethod,
    SetupConfig,
    WAFConfig,
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
        cc = ComputeConfig(volume_size=200)
        assert cc.volume_size == 200

    def test_minimum_volume_size(self):
        # ComputeConfig itself only enforces 20 GB (the call-server placeholder
        # path reuses this model with ~40 GB). The 100 GB IBL-platform floor
        # is enforced on `InfraConfig` for `DeploymentType.SINGLE` — see
        # TestSingleServerVolumeFloor below.
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
        assert cc.volume_size == 100
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
        assert sc.enable_ai is True


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

    def test_deployment_types(self):
        assert DeploymentType.SINGLE.value == "single-server"
        assert DeploymentType.MULTI.value == "multi-server"
        assert DeploymentType.CALL.value == "call-server"


# ---------------------------------------------------------------------------
# CallServerConfig
# ---------------------------------------------------------------------------


class TestCallServerConfig:
    def test_defaults(self):
        cfg = CallServerConfig()
        assert cfg.instance_type == "t3.large"
        assert cfg.volume_size == 40
        assert cfg.volume_type == "gp3"
        assert cfg.vpc_cidr == "10.1.0.0/16"  # distinct from single-server's 10.0/16
        assert cfg.enable_sip is False

    def test_custom_values(self):
        cfg = CallServerConfig(
            instance_type="m5.xlarge",
            volume_size=100,
            vpc_cidr="172.16.0.0/16",
            enable_sip=True,
        )
        assert cfg.instance_type == "m5.xlarge"
        assert cfg.volume_size == 100
        assert cfg.vpc_cidr == "172.16.0.0/16"
        assert cfg.enable_sip is True

    def test_volume_size_floor(self):
        with pytest.raises(ValidationError, match="at least 20"):
            CallServerConfig(volume_size=10)

    def test_attaches_to_infra_config(self, aws_credentials):
        """InfraConfig accepts a CallServerConfig under call_server field."""
        infra = InfraConfig(
            project_name="callenv",
            environment=Environment.PROD,
            deployment_type=DeploymentType.CALL,
            credentials=aws_credentials,
            network=NetworkConfig(vpc_cidr="10.1.0.0/16", vpn_ip="203.0.113.1"),
            compute=ComputeConfig(),
            call_server=CallServerConfig(enable_sip=True),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="call"),
            certificates=CertificateConfig(method=CertMethod.ACM),
            dns=DNSConfig(base_domain="example.com"),
        )
        assert infra.deployment_type == DeploymentType.CALL
        assert infra.call_server is not None
        assert infra.call_server.enable_sip is True

    def test_call_server_defaults_none(self, infra_config):
        """InfraConfig.call_server defaults to None for non-call deployments."""
        assert infra_config.call_server is None


# ---------------------------------------------------------------------------
# WAFConfig
# ---------------------------------------------------------------------------


class TestWAFConfig:
    def test_disabled_by_default(self):
        cfg = WAFConfig()
        assert cfg.enabled is False
        assert cfg.allowed_ips == []

    def test_disabled_with_empty_ips_ok(self):
        # No ips required when disabled
        cfg = WAFConfig(enabled=False, allowed_ips=[])
        assert cfg.enabled is False

    def test_enabled_requires_ips(self):
        with pytest.raises(ValidationError, match="allowed_ips is empty"):
            WAFConfig(enabled=True, allowed_ips=[])

    def test_bare_ip_normalised_to_slash_32(self):
        cfg = WAFConfig(enabled=True, allowed_ips=["203.0.113.7"])
        assert cfg.allowed_ips == ["203.0.113.7/32"]

    def test_cidr_passes_through(self):
        cfg = WAFConfig(enabled=True, allowed_ips=["10.0.0.0/16"])
        assert cfg.allowed_ips == ["10.0.0.0/16"]

    def test_mixed_bare_and_cidr(self):
        cfg = WAFConfig(
            enabled=True,
            allowed_ips=["198.51.100.7", "10.0.0.0/24", "192.0.2.42"],
        )
        assert cfg.allowed_ips == [
            "198.51.100.7/32",
            "10.0.0.0/24",
            "192.0.2.42/32",
        ]

    def test_invalid_token_raises(self):
        with pytest.raises(ValidationError, match="Invalid IP or CIDR"):
            WAFConfig(enabled=True, allowed_ips=["not-an-ip"])

    def test_blank_tokens_are_filtered(self):
        cfg = WAFConfig(enabled=True, allowed_ips=["", "  ", "203.0.113.7"])
        assert cfg.allowed_ips == ["203.0.113.7/32"]

    def test_cidr_host_bits_zeroed(self):
        # strict=False on ip_network — input with host bits becomes the network
        cfg = WAFConfig(enabled=True, allowed_ips=["10.0.0.42/24"])
        assert cfg.allowed_ips == ["10.0.0.0/24"]

    def test_attaches_to_infra_config(self, aws_credentials):
        infra = InfraConfig(
            project_name="wafenv",
            environment=Environment.PROD,
            credentials=aws_credentials,
            network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="203.0.113.1"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="waf"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
            waf=WAFConfig(enabled=True, allowed_ips=["203.0.113.7"]),
        )
        assert infra.waf is not None
        assert infra.waf.enabled is True
        assert infra.waf.allowed_ips == ["203.0.113.7/32"]

    def test_waf_defaults_none_on_infra_config(self, infra_config):
        assert infra_config.waf is None


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


# ---------------------------------------------------------------------------
# IngressEntry
# ---------------------------------------------------------------------------


class TestIngressEntry:
    def test_create_basic(self):
        entry = IngressEntry(name="stg1", domain="stg1.example.com")
        assert entry.name == "stg1"
        assert entry.domain == "stg1.example.com"
        assert entry.created_at is not None

    def test_roundtrip_json(self):
        entry = IngressEntry(name="stg2", domain="stg2.example.com")
        data = entry.model_dump(mode="json")
        restored = IngressEntry.model_validate(data)
        assert restored.name == entry.name
        assert restored.domain == entry.domain


class TestIngressLockConfig:
    def test_defaults(self):
        cfg = IngressLockConfig()
        assert cfg.backend == "local"
        assert cfg.bucket == ""
        assert cfg.prefix == "ingress-locks"

    def test_s3_backend(self):
        cfg = IngressLockConfig(backend="s3", bucket="my-bucket", prefix="locks")
        assert cfg.backend == "s3"
        assert cfg.bucket == "my-bucket"


class TestIngressRegistry:
    def test_empty_defaults(self):
        reg = IngressRegistry()
        assert reg.entries == []
        assert reg.lock.backend == "local"

    def test_with_entries_and_lock(self):
        reg = IngressRegistry(
            entries=[IngressEntry(name="a", domain="a.example.com")],
            lock=IngressLockConfig(backend="s3", bucket="b"),
        )
        assert len(reg.entries) == 1
        assert reg.lock.backend == "s3"

    def test_roundtrip_json(self):
        reg = IngressRegistry(
            entries=[IngressEntry(name="a", domain="a.example.com")],
            lock=IngressLockConfig(backend="s3", bucket="b", prefix="p"),
        )
        data = reg.model_dump(mode="json")
        restored = IngressRegistry.model_validate(data)
        assert restored.entries[0].name == "a"
        assert restored.lock.bucket == "b"
