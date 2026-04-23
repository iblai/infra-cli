"""Pydantic models — the contract between the wizard and Terraform."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AuthMethod(str, Enum):
    PROFILE = "profile"
    ACCESS_KEY = "access_key"
    ENVIRONMENT = "environment"


class SSHKeyMethod(str, Enum):
    GENERATE = "generate"
    EXISTING_FILE = "existing_file"
    AWS_KEYPAIR = "aws_keypair"


class CertMethod(str, Enum):
    ACM = "acm"
    UPLOAD = "upload"
    NONE = "none"


class Environment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class DeploymentType(str, Enum):
    SINGLE = "single-server"
    MULTI = "multi-server"
    CALL = "call-server"


# ---------------------------------------------------------------------------
# AWS Region metadata
# ---------------------------------------------------------------------------

AWS_REGIONS: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "Europe (Ireland)",
    "eu-west-2": "Europe (London)",
    "eu-west-3": "Europe (Paris)",
    "eu-central-1": "Europe (Frankfurt)",
    "eu-north-1": "Europe (Stockholm)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ca-central-1": "Canada (Central)",
    "sa-east-1": "South America (Sao Paulo)",
    "me-south-1": "Middle East (Bahrain)",
    "af-south-1": "Africa (Cape Town)",
}

# Common instance types with human-readable descriptions
INSTANCE_TYPES: dict[str, str] = {
    "t3.xlarge": "4 vCPU,  16 GB RAM — Small workloads",
    "t3.2xlarge": "8 vCPU,  32 GB RAM",
    "m5.2xlarge": "8 vCPU,  32 GB RAM — Compute optimized",
    "m5.4xlarge": "16 vCPU, 64 GB RAM — Large workloads",
    "r5.2xlarge": "8 vCPU,  64 GB RAM — Memory optimized",
}

# IBL platform subdomains generated from the base domain
IBL_SUBDOMAINS: list[str] = [
    "learn.{domain}",
    "preview.learn.{domain}",
    "studio.learn.{domain}",
    "apps.learn.{domain}",
    "meilisearch.learn.{domain}",
    "api.data.{domain}",
    "api.{domain}",
    "asgi.data.{domain}",
    "llm.data.{domain}",
    "mentor.data.{domain}",
    "web.data.{domain}",
    "base.manager.{domain}",
    "auth.{domain}",
    "mentorai.{domain}",
    "monitor.{domain}",
    "flowise.{domain}",
    "skillsai.{domain}",
    "platform.{domain}",
    "prometheus.{domain}",
]


# ---------------------------------------------------------------------------
# Config sections
# ---------------------------------------------------------------------------

class AWSCredentials(BaseModel):
    method: AuthMethod
    profile: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    region: str

    # Populated after validation
    account_id: str | None = None
    arn: str | None = None


class NetworkConfig(BaseModel):
    vpc_cidr: str = "10.0.0.0/16"
    vpn_ip: str  # e.g. "203.0.113.42"

    @field_validator("vpn_ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        import ipaddress
        ipaddress.ip_address(v)  # raises ValueError if invalid
        return v


class ComputeConfig(BaseModel):
    instance_type: str = "t3.2xlarge"
    volume_size: int = 50
    volume_type: str = "gp3"
    ami_id: str | None = None

    @field_validator("volume_size")
    @classmethod
    def validate_volume(cls, v: int) -> int:
        if v < 20:
            raise ValueError("Volume size must be at least 20 GB")
        return v


class SSHConfig(BaseModel):
    method: SSHKeyMethod
    key_name: str
    public_key: str | None = None
    private_key_path: Path | None = None


class CertificateConfig(BaseModel):
    method: CertMethod
    hosted_zone_id: str | None = None
    cert_body: str | None = None
    cert_private_key: str | None = None
    cert_chain: str | None = None


class DNSConfig(BaseModel):
    base_domain: str
    use_route53: bool = False
    hosted_zone_id: str | None = None

    @property
    def subdomains(self) -> list[str]:
        return [s.format(domain=self.base_domain) for s in IBL_SUBDOMAINS]


class MultiServerConfig(BaseModel):
    """Configuration for multi-server deployments (app servers + services server)."""
    app_server_count: int = 2
    app_server_instance_type: str = "t3.2xlarge"
    app_server_volume_size: int = 250
    services_instance_type: str = "t3.2xlarge"
    services_volume_size: int = 500

    # Optional managed services
    enable_mysql: bool = False
    mysql_instance_class: str = "db.r6g.large"
    mysql_storage_size: int = 300
    enable_postgres: bool = False
    postgres_instance_class: str = "db.r6g.large"
    postgres_storage_size: int = 300
    enable_redis: bool = False
    redis_instance_type: str = "cache.r6g.xlarge"

    # Secrets — generated at runtime, never serialized to state.json
    mysql_password: str | None = Field(default=None, exclude=True)
    postgres_password: str | None = Field(default=None, exclude=True)
    redis_auth_token: str | None = Field(default=None, exclude=True)

    @field_validator("app_server_count")
    @classmethod
    def validate_app_server_count(cls, v: int) -> int:
        if v < 2:
            raise ValueError("App server count must be at least 2")
        if v > 10:
            raise ValueError("App server count must be 10 or fewer")
        return v

    @field_validator("app_server_volume_size", "services_volume_size")
    @classmethod
    def validate_volume_sizes(cls, v: int) -> int:
        if v < 20:
            raise ValueError("Volume size must be at least 20 GB")
        return v


class CallServerConfig(BaseModel):
    """Configuration for call-server (LiveKit) deployments.

    Deployed as a standalone VM in an isolated VPC. Exposes the full LiveKit
    port set: API/WebSocket :7880, ICE/TCP :7881, ICE/UDP-mux :7882,
    ICE/UDP 50000-60000, TURN/TLS :5349, TURN/UDP :3478. SIP stack
    (5060/5061/10000-20000) is gated on `enable_sip`.
    """
    instance_type: str = "t3.large"
    volume_size: int = 40
    volume_type: str = "gp3"
    vpc_cidr: str = "10.1.0.0/16"  # distinct default from single-server (10.0/16)
    enable_sip: bool = False

    @field_validator("volume_size")
    @classmethod
    def validate_volume(cls, v: int) -> int:
        if v < 20:
            raise ValueError("Volume size must be at least 20 GB")
        return v


# ---------------------------------------------------------------------------
# Top-level config — the single contract
# ---------------------------------------------------------------------------

class InfraConfig(BaseModel):
    project_name: str
    environment: Environment
    deployment_type: DeploymentType = DeploymentType.SINGLE
    credentials: AWSCredentials
    network: NetworkConfig
    compute: ComputeConfig
    multi_server: MultiServerConfig | None = None
    call_server: CallServerConfig | None = None
    ssh: SSHConfig
    certificates: CertificateConfig
    dns: DNSConfig

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Project name must be alphanumeric (hyphens/underscores allowed)")
        if len(v) > 32:
            raise ValueError("Project name must be 32 characters or fewer")
        return v

    @property
    def resource_prefix(self) -> str:
        return f"{self.project_name}-{self.environment.value}"


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

class ProjectState(BaseModel):
    name: str
    provider: str = "aws"
    status: Literal["initialized", "created", "failed", "destroyed"] = "initialized"
    config: InfraConfig
    outputs: dict | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    workspace_path: str = ""
    setup_status: Literal["pending", "running", "completed", "failed"] | None = None
    setup_completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Setup config — contract between setup prompts and AnsibleRunner
# ---------------------------------------------------------------------------

class SetupConfig(BaseModel):
    """Variables needed to bootstrap a provisioned VM. Never persisted to disk."""
    ssh_private_key_path: Path
    ssh_user: str = "ubuntu"
    target_host: str
    base_domain: str
    edx_version: str = "sumac"
    env_config: str = "single-server"
    cli_ops_release_tag: str = "3.19.0"
    prod_images_tag: str = "main"
    enable_ai: bool = True
    is_resetup: bool = False
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_default_region: str
    git_access_token: str
    openai_api_key: str = ""
    admin_username: str = "ibl_admin"
    admin_email: str = ""
    admin_password: str = ""


# ---------------------------------------------------------------------------
# Ingress — pre-provisioned domain endpoints
# ---------------------------------------------------------------------------

class IngressEntry(BaseModel):
    """A pre-provisioned ingress endpoint (domain + certs + DNS)."""
    name: str
    domain: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IngressLockConfig(BaseModel):
    """Lock backend configuration for ingress slot management."""
    backend: Literal["local", "s3"] = "local"
    bucket: str = ""
    prefix: str = "ingress-locks"


class IngressRegistry(BaseModel):
    """Ingress registry with entries and optional lock backend."""
    entries: list[IngressEntry] = Field(default_factory=list)
    lock: IngressLockConfig = Field(default_factory=IngressLockConfig)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
