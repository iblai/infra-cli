"""Global test fixtures for iblai-infra."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

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
    SetupConfig,
    SSHConfig,
    SSHKeyMethod,
)


@pytest.fixture
def aws_credentials() -> AWSCredentials:
    return AWSCredentials(
        method=AuthMethod.ACCESS_KEY,
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        account_id="123456789012",
        arn="arn:aws:iam::123456789012:user/testuser",
    )


@pytest.fixture
def infra_config(aws_credentials: AWSCredentials) -> InfraConfig:
    return InfraConfig(
        project_name="testproject",
        environment=Environment.DEV,
        credentials=aws_credentials,
        network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="203.0.113.42"),
        compute=ComputeConfig(instance_type="t3.2xlarge", volume_size=50, volume_type="gp3"),
        ssh=SSHConfig(
            method=SSHKeyMethod.GENERATE,
            key_name="testproject-dev",
            public_key="ssh-rsa AAAA...",
            private_key_path=Path("/tmp/testkey.pem"),
        ),
        certificates=CertificateConfig(method=CertMethod.NONE),
        dns=DNSConfig(base_domain="example.com"),
    )


@pytest.fixture
def project_state(infra_config: InfraConfig, tmp_path: Path) -> ProjectState:
    return ProjectState(
        name="testproject",
        provider="aws",
        status="created",
        config=infra_config,
        outputs={"instance_public_ip": "54.123.45.67", "alb_dns_name": "alb-123.us-east-1.elb.amazonaws.com"},
        created_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        workspace_path=str(tmp_path),
    )


@pytest.fixture
def setup_config(tmp_path: Path) -> SetupConfig:
    key_path = tmp_path / "key.pem"
    key_path.write_text("fake-key")
    key_path.chmod(0o600)
    return SetupConfig(
        ssh_private_key_path=key_path,
        ssh_user="ubuntu",
        target_host="54.123.45.67",
        base_domain="example.com",
        edx_version="sumac",
        env_config="single-server",
        git_access_token="ghp_testtoken123",
        aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
        aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        aws_default_region="us-east-1",
    )


@pytest.fixture
def workspace_root(tmp_path: Path):
    """Override WORKSPACE_ROOT to a temp directory."""
    root = tmp_path / "projects"
    root.mkdir()
    with mock.patch("iblai_infra.terraform.state.WORKSPACE_ROOT", root):
        yield root
