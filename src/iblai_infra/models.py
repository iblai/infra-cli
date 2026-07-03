"""Pydantic models — the contract between the wizard and Terraform."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_repo_path(value: str) -> tuple[str, str | None]:
    """Split a 'repo' or 'repo/subdir' string into (repo, subdir).

    Used by the ansible runner to point installs at a package inside a
    monorepo. `iblai-cli-ops` -> ('iblai-cli-ops', None);
    `<client>-iblai-infra-ops/iblai-cli-ops` -> ('<client>-iblai-infra-ops',
    'iblai-cli-ops').
    """
    cleaned = (value or "").strip().strip("/")
    if "/" in cleaned:
        repo, _, subdir = cleaned.partition("/")
        return repo, subdir or None
    return cleaned, None


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
    ACM = "acm"           # AWS ACM (DNS-validated)
    MANAGED = "managed"   # GCP Google-managed SSL certificate
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


class CloudProvider(str, Enum):
    """Which cloud a deployment targets. Selects the Terraform template tree
    (`templates/<cloud>/<topology>`) and the credential/runner branch."""
    AWS = "aws"
    GCP = "gcp"


class GCPAuthMethod(str, Enum):
    ADC = "adc"                              # Application Default Credentials
    SERVICE_ACCOUNT_KEY = "service_account_key"  # path to a SA key JSON


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

# Common instance types with human-readable descriptions (single/multi-server).
INSTANCE_TYPES: dict[str, str] = {
    "t3.xlarge": "4 vCPU,  16 GB RAM — Small workloads",
    "t3.2xlarge": "8 vCPU,  32 GB RAM",
    "m5.2xlarge": "8 vCPU,  32 GB RAM — Compute optimized",
    "m5.4xlarge": "16 vCPU, 64 GB RAM — Large workloads",
    "r5.2xlarge": "8 vCPU,  64 GB RAM — Memory optimized",
}

# RAM (in GB) for the instance types we publish in the picker. Used by the
# prompt + launch flows to warn operators when they pick a 32 GB box —
# AI-enabled platforms benefit substantially from 64 GB.
INSTANCE_RAM_GB: dict[str, int] = {
    "t3.xlarge": 16,
    "t3.2xlarge": 32,
    "m5.2xlarge": 32,
    "m5.4xlarge": 64,
    "r5.2xlarge": 64,
}


def instance_ram_gb(instance_type: str) -> int | None:
    """Return RAM in GB for a known instance type, or None for unknown/custom."""
    return INSTANCE_RAM_GB.get((instance_type or "").strip())

# LiveKit (call-server) sizing recommendations. Per LiveKit's self-hosting
# guide, SFU-only workloads fit on 2 vCPU boxes; egress/recording benefits
# from CPU-optimized (c5) families.
CALL_INSTANCE_TYPES: dict[str, str] = {
    "t3.medium":  "2 vCPU,  4 GB RAM — SFU-only, small rooms",
    "t3.large":   "2 vCPU,  8 GB RAM — SFU-only, moderate rooms (default)",
    "t3.xlarge":  "4 vCPU, 16 GB RAM — with egress / medium rooms",
    "c5.xlarge":  "4 vCPU,  8 GB RAM — CPU-optimized for transcoding",
    "c5.2xlarge": "8 vCPU, 16 GB RAM — heavy production / recording",
}

# ---------------------------------------------------------------------------
# GCP metadata (regions, machine types, disk types)
# ---------------------------------------------------------------------------

GCP_REGIONS: dict[str, str] = {
    "us-central1": "US Central (Iowa)",
    "us-east1": "US East (South Carolina)",
    "us-east4": "US East (N. Virginia)",
    "us-west1": "US West (Oregon)",
    "us-west2": "US West (Los Angeles)",
    "northamerica-northeast1": "Montreal",
    "southamerica-east1": "Sao Paulo",
    "europe-west1": "Belgium",
    "europe-west2": "London",
    "europe-west3": "Frankfurt",
    "europe-west4": "Netherlands",
    "europe-north1": "Finland",
    "asia-south1": "Mumbai",
    "asia-southeast1": "Singapore",
    "asia-northeast1": "Tokyo",
    "australia-southeast1": "Sydney",
}

# Single-server machine types with human-readable descriptions.
GCP_MACHINE_TYPES: dict[str, str] = {
    "e2-standard-4": "4 vCPU,  16 GB RAM - Small workloads",
    "e2-standard-8": "8 vCPU,  32 GB RAM - Balanced (default)",
    "n2-standard-8": "8 vCPU,  32 GB RAM - Compute optimized",
    "n2-highmem-8": "8 vCPU,  64 GB RAM - Memory optimized",
    "e2-standard-16": "16 vCPU, 64 GB RAM - Large workloads",
}

# RAM (in GB) for the machine types we publish. Mirrors INSTANCE_RAM_GB so the
# prompt/launch flows can warn on 32 GB boxes for AI-enabled platforms.
GCP_MACHINE_RAM_GB: dict[str, int] = {
    "e2-standard-4": 16,
    "e2-standard-8": 32,
    "n2-standard-8": 32,
    "n2-highmem-8": 64,
    "e2-standard-16": 64,
}

GCP_DISK_TYPES: dict[str, str] = {
    "pd-balanced": "Balanced persistent disk (default)",
    "pd-ssd": "Performance SSD persistent disk",
    "pd-standard": "Standard persistent disk (HDD)",
}


def gcp_machine_ram_gb(machine_type: str) -> int | None:
    """Return RAM in GB for a known GCP machine type, or None for unknown."""
    return GCP_MACHINE_RAM_GB.get((machine_type or "").strip())


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


class GCPCredentials(BaseModel):
    """GCP auth + targeting. The Terraform google provider reads credentials
    from Application Default Credentials (ADC) or a service-account key JSON via
    GOOGLE_APPLICATION_CREDENTIALS; this model records which, plus the project
    and the region/zone the single VM lives in."""
    method: GCPAuthMethod = GCPAuthMethod.ADC
    project_id: str
    region: str = "us-central1"
    zone: str = "us-central1-a"
    # Path to a service-account key JSON (when method = service_account_key).
    credentials_file: str | None = None

    # Populated after validation
    account: str | None = None
    project_number: str | None = None


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
    volume_size: int = 100
    volume_type: str = "gp3"
    ami_id: str | None = None

    # Floor of 20 GB here is the lower bound for *any* compute config —
    # call-server reuses ComputeConfig as the synced-from-CallServerConfig
    # placeholder and runs LiveKit on a small disk (~40 GB). The
    # IBL-platform-minimum 100 GB floor is enforced on `InfraConfig` (and
    # at the prompt / CLI / .env input layers) only for `DeploymentType.SINGLE`,
    # since multi-server uses `MultiServerConfig.{app_server,services}_volume_size`
    # and multi's own validator handles that case.
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
    # GCP Cloud DNS analogs of hosted_zone_id. `dns_zone_name` is the managed
    # zone's resource name; `create_dns_zone` asks Terraform to create it (and
    # emit nameservers for registrar delegation) rather than use an existing one.
    dns_zone_name: str | None = None
    create_dns_zone: bool = False

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
        if v < 100:
            raise ValueError("Volume size must be at least 100 GB")
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


class WAFConfig(BaseModel):
    """Optional AWS WAFv2 Web ACL attached to the single-server ALB.

    `allowed_ips` accepts both bare IPv4 addresses (e.g. "203.0.113.7") and
    CIDR blocks (e.g. "10.0.0.0/16"). Bare IPs are normalised to /32 at
    validation time because AWS WAFv2 IPSets require CIDR form. When
    `enabled=True`, at least one IP/CIDR must be supplied — an empty allowlist
    would lock the operator out of admin surfaces (Swagger, Studio, /admin,
    /data).
    """
    enabled: bool = False
    allowed_ips: list[str] = Field(default_factory=list)

    @field_validator("allowed_ips")
    @classmethod
    def _normalize_ips(cls, v: list[str]) -> list[str]:
        import ipaddress
        out: list[str] = []
        for raw in v:
            s = (raw or "").strip()
            if not s:
                continue
            try:
                ipaddress.ip_address(s)
                out.append(f"{s}/32")
                continue
            except ValueError:
                pass
            try:
                net = ipaddress.ip_network(s, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid IP or CIDR: {raw!r}") from exc
            out.append(str(net))
        return out

    @model_validator(mode="after")
    def _require_ips_when_enabled(self) -> "WAFConfig":
        if self.enabled and not self.allowed_ips:
            raise ValueError(
                "WAF is enabled but allowed_ips is empty. Provide at least one "
                "IP or CIDR for the admin allowlist, or disable WAF."
            )
        return self


# ---------------------------------------------------------------------------
# Top-level config — the single contract
# ---------------------------------------------------------------------------

class InfraConfig(BaseModel):
    project_name: str
    environment: Environment
    # Which cloud to target. Defaults to AWS so existing state.json (and every
    # AWS code path) is unchanged. GCP deployments set this and populate
    # `gcp_credentials` instead of `credentials`.
    cloud: CloudProvider = CloudProvider.AWS
    deployment_type: DeploymentType = DeploymentType.SINGLE
    # Exactly one credential block is required, keyed by `cloud` (enforced by
    # `_validate_credentials_for_cloud`). `credentials` stays first + optional so
    # existing AWS configs deserialize unchanged.
    credentials: AWSCredentials | None = None
    gcp_credentials: GCPCredentials | None = None
    network: NetworkConfig
    compute: ComputeConfig
    multi_server: MultiServerConfig | None = None
    call_server: CallServerConfig | None = None
    ssh: SSHConfig
    certificates: CertificateConfig
    dns: DNSConfig
    # Optional WAFv2 protection on the ALB. Single-server only — multi-server
    # also has an ALB but is out of scope for this iteration; call-server has
    # no ALB. Defaults to None (no WAF).
    waf: WAFConfig | None = None

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Project name must be alphanumeric (hyphens/underscores allowed)")
        if len(v) > 32:
            raise ValueError("Project name must be 32 characters or fewer")
        return v

    # Enforce the 100 GB platform-disk floor on SINGLE deployments. MULTI is
    # already covered by `MultiServerConfig.validate_volume_sizes`; CALL uses
    # `CallServerConfig.volume_size` (LiveKit only needs ~40 GB) and reuses
    # ComputeConfig as a placeholder, so we don't enforce a 100 GB floor on it.
    @model_validator(mode="after")
    def _validate_single_server_volume_size(self) -> "InfraConfig":
        if self.deployment_type == DeploymentType.SINGLE and self.compute.volume_size < 100:
            raise ValueError(
                "Single-server volume size must be at least 100 GB "
                f"(got {self.compute.volume_size})"
            )
        return self

    @model_validator(mode="after")
    def _validate_credentials_for_cloud(self) -> "InfraConfig":
        if self.cloud == CloudProvider.AWS and self.credentials is None:
            raise ValueError("AWS deployments require `credentials` (AWSCredentials)")
        if self.cloud == CloudProvider.GCP and self.gcp_credentials is None:
            raise ValueError("GCP deployments require `gcp_credentials` (GCPCredentials)")
        return self

    @property
    def resource_prefix(self) -> str:
        return f"{self.project_name}-{self.environment.value}"

    @property
    def region(self) -> str:
        """Region for the active cloud, regardless of which credential block is set."""
        if self.cloud == CloudProvider.GCP and self.gcp_credentials:
            return self.gcp_credentials.region
        return self.credentials.region if self.credentials else ""


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

# Usernames reserved for system / platform-internal use. The ibl_spa role
# looks up `ibl_admin` to own the `spa-sso` and `ibl_web` OAuth2 Application
# records on the LMS — that user is created by the platform's own bootstrap
# (`ibl edx` / `ibl dm` launch flows) before ibl_spa runs. Operators must
# pick a different name for their human superuser so the system account
# stays separate.
RESERVED_ADMIN_USERNAMES: frozenset[str] = frozenset({"ibl_admin"})


def is_reserved_admin_username(value: str) -> bool:
    """Return True if `value` collides with a reserved system username."""
    return (value or "").strip().lower() in RESERVED_ADMIN_USERNAMES


# Platform identifiers reserved for system / platform-internal use. `main`
# is the IBL default tenant the platform itself creates and maintains via
# `ibl launch`. Operators can't pick `main` as a tenant name — instead they
# leave the field blank/unset, which silently resolves to `main` for SSO
# backwards-compat (backend_name=`main-oauth2`) and skips the tenant
# launcher (see `ibl_tenant_platform` ansible role).
RESERVED_PLATFORM_NAMES: frozenset[str] = frozenset({"main"})


def is_reserved_platform_name(value: str) -> bool:
    """Return True if `value` collides with a reserved system platform name."""
    return (value or "").strip().lower() in RESERVED_PLATFORM_NAMES


class SetupConfig(BaseModel):
    """Variables needed to bootstrap a provisioned VM. Never persisted to disk."""
    ssh_private_key_path: Path
    ssh_user: str = "ubuntu"
    target_host: str
    base_domain: str
    edx_version: str = "sumac"
    env_config: str = "single-server"
    # iblai-cli-ops install tag. Empty = "resolve from the prod-images pin":
    # iblai-prod-images' pyproject.toml pins ibl-cli via [tool.uv.sources]
    # (rev = "<tag>"), and the interactive/env flows resolve that pin via
    # `env_utils.resolve_pinned_cli_ops_tag`. AnsibleRunner falls back to
    # "main" if the field is still empty at run time.
    cli_ops_release_tag: str = ""
    prod_images_tag: str = "main"
    enable_ai: bool = True
    is_resetup: bool = False
    create_playwright_platforms: bool = False
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_default_region: str
    git_access_token: str
    # GitHub org + repo names for the two private packages this setup
    # installs (iblai-prod-images directly, iblai-cli-ops transitively).
    # Configurable so a fork or a non-iblai deployment can point at its
    # own repos. Defaults reflect the canonical IBL deployment.
    github_org: str = "iblai"
    # Each repo field accepts either a bare repo name (`iblai-cli-ops`) or a
    # `repo/subdir` path (`<client>-iblai-infra-ops/iblai-cli-ops`) to point at
    # a package inside a monorepo. Parsed by `parse_repo_path()` before the
    # install URL is built.
    cli_ops_repo: str = "iblai-cli-ops"
    prod_images_repo: str = "iblai-prod-images"
    openai_api_key: str = ""
    admin_username: str = "platform_admin"
    admin_email: str = ""
    admin_password: str = ""
    # SMTP for outbound email (magic-link tests etc.). Disabled by default;
    # the ansible role no-ops unless smtp_enabled is true. Password is
    # excluded from serialization so it's never written to state.json.
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = Field(default="", exclude=True)
    smtp_sender_email: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    # Stripe billing config (pk=1 of dl_iblai_services_app.StripeAPIKey for the
    # 'main' platform + GlobalConfiguration['IBL_CURRENT_STRIPE_MODE']).
    # Disabled by default; the role no-ops unless stripe_enabled is true.
    # All four secret-shaped fields are Field(exclude=True) so they never land
    # in state.json — they ride extra_vars to ansible at run time only.
    stripe_enabled: bool = False
    stripe_mode: str = "test"  # "test" or "live"
    stripe_secret_key: str = Field(default="", exclude=True)
    stripe_pub_key: str = Field(default="", exclude=True)
    stripe_pricing_table_id: str = ""
    stripe_pricing_table_id_returning: str = ""
    stripe_webhook_secret: str = Field(default="", exclude=True)
    stripe_connect_webhook_secret: str = Field(default="", exclude=True)
    # Google SSO — adds an `OAuth2ProviderConfig` row in the LMS for the
    # `google-oauth2` python-social-auth backend. Disabled by default; the
    # ansible role no-ops unless google_sso_enabled is true. Client secret
    # is excluded from serialization so it never lands in state.json — it
    # rides extra_vars to ansible at run time only.
    google_sso_enabled: bool = False
    google_sso_client_id: str = ""
    google_sso_client_secret: str = Field(default="", exclude=True)
    google_sso_organization: str = ""
    # Platform name — single-token identifier used by the SSO ansible roles
    # to derive `backend_name = <platform_name>-oauth2` and the
    # `other_settings.platform_key`. Defaults to "main" (canonical IBL
    # single-tenant). Operator may override during setup with a
    # tenant-specific value. Lowercased + stripped on input. Always
    # populated; the SSO roles read it whether or not their feature flag
    # is enabled.
    platform_name: str = "main"
    # Microsoft (Azure AD) SSO — adds an `OAuth2ProviderConfig` row in the
    # LMS for the `azuread-oauth2` slug (with backend_name derived from
    # platform_name) AND an `IBL_EDX_BASE_OAUTH_SSO_BACKEND` block under
    # `IBL_EDX` in `/ibl/config.yml`. Disabled by default; the ansible
    # role no-ops unless microsoft_sso_enabled is true. Client secret is
    # excluded from serialization so it never lands in state.json — it
    # rides extra_vars to ansible at run time only.
    microsoft_sso_enabled: bool = False
    microsoft_sso_client_id: str = ""
    microsoft_sso_client_secret: str = Field(default="", exclude=True)
    microsoft_sso_tenant_id: str = ""
    microsoft_sso_organization: str = ""

    @field_validator("admin_username")
    @classmethod
    def _validate_admin_username(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("admin_username must not be empty")
        if s.lower() in RESERVED_ADMIN_USERNAMES:
            raise ValueError(
                f"'{s}' is reserved for system use; pick a different admin username"
            )
        return s


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
