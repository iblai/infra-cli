"""Build a fully-validated GCP ``InfraConfig`` from a ``.env``-style dict.

The GCP counterpart to ``env_provision.build_infra_config_from_env``. Reached
via ``PROVIDER=gcp`` in the .env; powers ``iblai infra provision-env`` for GCP.
Single-server only.
"""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError

from iblai_infra import ui
from iblai_infra.env_utils import parse_bool
from iblai_infra.models import (
    CertificateConfig,
    CertMethod,
    CloudProvider,
    ComputeConfig,
    DeploymentType,
    DNSConfig,
    Environment,
    GCPAuthMethod,
    GCPCredentials,
    IBL_SUBDOMAINS,
    InfraConfig,
    NetworkConfig,
    SSHConfig,
    SSHKeyMethod,
)
from iblai_infra.providers.aws import detect_current_ip  # provider-neutral helper
from iblai_infra.prompts.infrastructure import generate_keypair

REQUIRED_GCP_KEYS: tuple[str, ...] = ("GCP_PROJECT_ID", "PROJECT_NAME", "DOMAIN", "VPN_IP")
VALID_CERT_METHODS: tuple[str, ...] = ("auto", "managed", "upload", "none")
VALID_SSH_METHODS: tuple[str, ...] = ("generate", "existing_file")


class EnvConfigError(typer.Exit):
    def __init__(self) -> None:
        super().__init__(code=1)


def _fail(message: str, *, hint: str | None = None) -> "EnvConfigError":
    ui.error(message)
    if hint:
        ui.muted(hint)
    return EnvConfigError()


def build_gcp_infra_config_from_env(
    env: dict[str, str], *, auto_delete_cnames: bool | None = None
) -> InfraConfig:
    """Validate ``env`` and return a GCP ``InfraConfig`` ready for TerraformRunner.

    Side effects mirror the AWS builder: validates GCP credentials, generates an
    SSH keypair on disk when ``SSH_KEY_METHOD=generate``, and removes conflicting
    Cloud DNS records when a managed cert is used (unless disabled).
    """
    if auto_delete_cnames is None:
        auto_delete_cnames = parse_bool(env.get("AUTO_DELETE_CONFLICTING_DNS"), default=True)

    missing = [k for k in REQUIRED_GCP_KEYS if not env.get(k)]
    if missing:
        ui.error("Missing required variables in .env (PROVIDER=gcp):")
        for k in missing:
            ui.muted(f"  - {k}")
        raise EnvConfigError()

    credentials = _build_gcp_credentials(env)

    # Validate credentials before doing anything else.
    from iblai_infra.providers import gcp as gcp_provider

    if not gcp_provider.is_available():
        raise _fail(
            "GCP support needs extra dependencies that aren't installed.",
            hint="Install with: uv sync --extra gcp  (or: pip install 'iblai-infra[gcp]')",
        )

    try:
        identity = gcp_provider.validate_credentials(credentials)
    except ValueError as exc:
        raise _fail(f"GCP credential check failed: {exc}")
    credentials.account = identity.account

    project_name, environment = _parse_project(env)
    network = _build_network(env)
    compute = _build_compute(env)
    ssh = _build_ssh(env, project_name, environment)
    dns, certificates = _build_dns_and_certs(
        env, credentials, auto_delete_cnames=auto_delete_cnames
    )

    try:
        return InfraConfig(
            project_name=project_name,
            environment=environment,
            cloud=CloudProvider.GCP,
            deployment_type=DeploymentType.SINGLE,
            gcp_credentials=credentials,
            network=network,
            compute=compute,
            ssh=ssh,
            certificates=certificates,
            dns=dns,
        )
    except ValidationError as exc:
        # Surface model-level failures (e.g. the 100 GB single-server floor)
        # as a clean message rather than a raw pydantic traceback.
        messages = "; ".join(e.get("msg", str(e)) for e in exc.errors())
        raise _fail(f"Invalid GCP configuration: {messages}")


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_gcp_credentials(env: dict[str, str]) -> GCPCredentials:
    project_id = env["GCP_PROJECT_ID"].strip()
    region = (env.get("GCP_REGION") or "us-central1").strip()
    zone = (env.get("GCP_ZONE") or f"{region}-a").strip()
    key_file = (env.get("GCP_CREDENTIALS_FILE") or "").strip()

    if key_file:
        path = Path(key_file).expanduser()
        if not path.exists():
            raise _fail(f"GCP_CREDENTIALS_FILE not found: {path}")
        return GCPCredentials(
            method=GCPAuthMethod.SERVICE_ACCOUNT_KEY,
            project_id=project_id,
            region=region,
            zone=zone,
            credentials_file=str(path),
        )
    # No key file -> Application Default Credentials.
    return GCPCredentials(
        method=GCPAuthMethod.ADC, project_id=project_id, region=region, zone=zone
    )


def _parse_project(env: dict[str, str]) -> tuple[str, Environment]:
    project_name = env["PROJECT_NAME"].strip().lower()
    env_raw = (env.get("ENVIRONMENT") or "staging").strip().lower()
    try:
        environment = Environment(env_raw)
    except ValueError:
        raise _fail(f"ENVIRONMENT={env_raw!r} is invalid.", hint="Use one of: dev, staging, prod.")
    return project_name, environment


def _build_network(env: dict[str, str]) -> NetworkConfig:
    # SUBNET_CIDR is the GCP-native name; accept VPC_CIDR as an alias.
    cidr = (env.get("SUBNET_CIDR") or env.get("VPC_CIDR") or "10.0.0.0/16").strip()
    vpn_raw = env["VPN_IP"].strip()
    if vpn_raw.lower() == "auto":
        detected = detect_current_ip()
        if not detected:
            raise _fail("VPN_IP=auto could not detect a public IP.", hint="Set VPN_IP to an IPv4 address.")
        vpn_ip = detected
    else:
        vpn_ip = vpn_raw
    try:
        return NetworkConfig(vpc_cidr=cidr, vpn_ip=vpn_ip)
    except ValueError as exc:
        raise _fail(f"Invalid network configuration: {exc}")


def _build_compute(env: dict[str, str]) -> ComputeConfig:
    machine_type = (env.get("MACHINE_TYPE") or "e2-standard-8").strip()
    disk_type = (env.get("DISK_TYPE") or "pd-balanced").strip()
    volume_raw = (env.get("VOLUME_SIZE") or "100").strip()
    image = (env.get("IMAGE") or "").strip() or None
    try:
        volume_size = int(volume_raw)
    except ValueError:
        raise _fail(f"VOLUME_SIZE={volume_raw!r} is not an integer.")
    try:
        # ComputeConfig reuses instance_type/volume_type to carry the GCP
        # machine type / disk type; ami_id carries a custom image.
        return ComputeConfig(
            instance_type=machine_type,
            volume_size=volume_size,
            volume_type=disk_type,
            ami_id=image,
        )
    except ValueError as exc:
        raise _fail(f"Invalid compute configuration: {exc}")


def _build_ssh(
    env: dict[str, str], project_name: str, environment: Environment
) -> SSHConfig:
    method_raw = (env.get("SSH_KEY_METHOD") or "generate").strip().lower()
    if method_raw not in VALID_SSH_METHODS:
        raise _fail(
            f"SSH_KEY_METHOD={method_raw!r} is invalid.",
            hint=f"Use one of: {', '.join(VALID_SSH_METHODS)}. (GCP has no aws_keypair.)",
        )
    method = SSHKeyMethod(method_raw)
    default_key_name = f"{project_name}-{environment.value}"

    if method == SSHKeyMethod.GENERATE:
        private_path, public_key = generate_keypair(default_key_name)
        ui.success(f"Key pair generated: [highlight]{private_path}[/highlight]")
        return SSHConfig(
            method=method,
            key_name=default_key_name,
            public_key=public_key,
            private_key_path=private_path,
        )

    # existing_file
    inline_pub = (env.get("SSH_PUBLIC_KEY") or "").strip()
    pub_path_raw = (env.get("SSH_PUBLIC_KEY_PATH") or "").strip()
    if pub_path_raw:
        pub_path = Path(pub_path_raw).expanduser()
        if not pub_path.exists():
            raise _fail(f"SSH_PUBLIC_KEY_PATH not found: {pub_path}")
        public_key = pub_path.read_text().strip()
        key_name = pub_path.stem
    elif inline_pub:
        public_key = inline_pub
        key_name = default_key_name
    else:
        raise _fail("SSH_KEY_METHOD=existing_file requires SSH_PUBLIC_KEY_PATH or SSH_PUBLIC_KEY.")
    priv_raw = (env.get("SSH_PRIVATE_KEY_PATH") or "").strip()
    priv_path = Path(priv_raw).expanduser() if priv_raw else None
    return SSHConfig(method=method, key_name=key_name, public_key=public_key, private_key_path=priv_path)


def _build_dns_and_certs(
    env: dict[str, str], credentials: GCPCredentials, *, auto_delete_cnames: bool
) -> tuple[DNSConfig, CertificateConfig]:
    base_domain = env["DOMAIN"].strip().lower()
    method_raw = (env.get("CERT_METHOD") or "auto").strip().lower()
    if method_raw not in VALID_CERT_METHODS:
        raise _fail(
            f"CERT_METHOD={method_raw!r} is invalid.",
            hint=f"Use one of: {', '.join(VALID_CERT_METHODS)}.",
        )

    explicit_zone = (env.get("DNS_ZONE_NAME") or "").strip() or None
    create_zone = parse_bool(env.get("CREATE_DNS_ZONE"), default=False)

    from iblai_infra.providers import gcp as gcp_provider

    # 'auto'/'managed' both consult Cloud DNS (unless creating the zone).
    matching: list = []
    if method_raw in ("auto", "managed") and not create_zone:
        zones = gcp_provider.list_managed_zones(credentials)
        matching = [
            z for z in zones
            if base_domain == z.dns_name or base_domain.endswith("." + z.dns_name)
        ]

    if method_raw == "auto":
        effective = "managed" if (matching or (create_zone and explicit_zone)) else "none"
        if effective == "none":
            ui.warning(
                f"No Cloud DNS zone matches {base_domain} - falling back to HTTP-only. "
                "Set CERT_METHOD=managed with DNS_ZONE_NAME, or CREATE_DNS_ZONE=true."
            )
    else:
        effective = method_raw

    if effective == "managed":
        zone_name = _resolve_zone_name(matching, explicit_zone, base_domain, create_zone)
        if not create_zone:
            _handle_dns_conflicts(
                credentials, zone_name, base_domain, auto_delete=auto_delete_cnames
            )
        return (
            DNSConfig(
                base_domain=base_domain,
                dns_zone_name=zone_name,
                create_dns_zone=create_zone,
            ),
            CertificateConfig(method=CertMethod.MANAGED, hosted_zone_id=None),
        )

    if effective == "upload":
        cert = _load_uploaded_cert(env)
        return DNSConfig(base_domain=base_domain), cert

    return DNSConfig(base_domain=base_domain), CertificateConfig(method=CertMethod.NONE)


def _resolve_zone_name(
    matching: list, explicit_zone: str | None, base_domain: str, create_zone: bool
) -> str:
    if create_zone:
        if not explicit_zone:
            raise _fail(
                "CREATE_DNS_ZONE=true requires DNS_ZONE_NAME (the zone resource name to create).",
            )
        return explicit_zone
    if explicit_zone:
        return explicit_zone
    if not matching:
        raise _fail(
            f"CERT_METHOD=managed but no Cloud DNS zone matches {base_domain}.",
            hint="Set DNS_ZONE_NAME, or CREATE_DNS_ZONE=true, or use CERT_METHOD=none.",
        )
    if len(matching) > 1:
        raise _fail(
            f"Multiple Cloud DNS zones match {base_domain}: {', '.join(z.name for z in matching)}.",
            hint="Disambiguate by setting DNS_ZONE_NAME.",
        )
    return matching[0].name


def _handle_dns_conflicts(
    credentials: GCPCredentials, zone_name: str, base_domain: str, *, auto_delete: bool
) -> None:
    from iblai_infra.providers import gcp as gcp_provider

    subdomains = [base_domain] + [s.format(domain=base_domain) for s in IBL_SUBDOMAINS]
    conflicts = gcp_provider.find_conflicting_records(credentials, zone_name, subdomains)
    if not conflicts:
        return
    if not auto_delete:
        ui.error(f"Found {len(conflicts)} conflicting record(s) in zone {zone_name}:")
        for c in conflicts:
            ui.muted(f"  {c.record_type}  {c.name.rstrip('.')}  ->  {', '.join(c.rrdatas)}")
        ui.muted("Set AUTO_DELETE_CONFLICTING_DNS=true (or remove them manually) and retry.")
        raise EnvConfigError()
    ui.warning(f"Removing {len(conflicts)} conflicting record(s) in zone {zone_name}")
    gcp_provider.delete_records(credentials, zone_name, conflicts)


def _load_uploaded_cert(env: dict[str, str]) -> CertificateConfig:
    body_path = (env.get("CERT_BODY_PATH") or "").strip()
    key_path = (env.get("CERT_KEY_PATH") or "").strip()
    missing = [k for k, v in (("CERT_BODY_PATH", body_path), ("CERT_KEY_PATH", key_path)) if not v]
    if missing:
        raise _fail("CERT_METHOD=upload requires: " + ", ".join(missing))
    body_p = Path(body_path).expanduser()
    key_p = Path(key_path).expanduser()
    if not body_p.exists():
        raise _fail(f"CERT_BODY_PATH not found: {body_p}")
    if not key_p.exists():
        raise _fail(f"CERT_KEY_PATH not found: {key_p}")
    return CertificateConfig(
        method=CertMethod.UPLOAD,
        cert_body=body_p.read_text(),
        cert_private_key=key_p.read_text(),
    )
