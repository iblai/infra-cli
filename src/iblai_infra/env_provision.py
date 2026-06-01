"""Build a fully-validated `InfraConfig` from a `.env`-style dict.

This is the non-interactive counterpart to the `provision` wizard's
prompts. It powers `iblai infra provision-env`. Single-server only —
multi-server and call-server routes are explicitly rejected so this
file stays small and the failure mode is obvious.
"""

from __future__ import annotations

from pathlib import Path

import typer

from iblai_infra import ui
from iblai_infra.env_utils import parse_bool
from iblai_infra.models import (
    AuthMethod,
    AWSCredentials,
    CertMethod,
    CertificateConfig,
    ComputeConfig,
    DeploymentType,
    DNSConfig,
    Environment,
    IBL_SUBDOMAINS,
    InfraConfig,
    NetworkConfig,
    SSHConfig,
    SSHKeyMethod,
    WAFConfig,
)
from iblai_infra.providers.aws import (
    delete_route53_records,
    detect_current_ip,
    find_conflicting_records,
    get_session,
    list_hosted_zones,
    list_key_pairs,
    validate_credentials,
)
from iblai_infra.prompts.infrastructure import generate_keypair


# Keys we accept in the .env. Anything else is silently ignored so
# operators can keep launch-env keys + provision-env keys in one file.
REQUIRED_BASE_KEYS: tuple[str, ...] = ("PROJECT_NAME", "DOMAIN", "VPN_IP")
VALID_DEPLOYMENT_TYPES: tuple[str, ...] = ("single-server",)
VALID_CERT_METHODS: tuple[str, ...] = ("auto", "acm", "upload", "none")
VALID_SSH_METHODS: tuple[str, ...] = ("generate", "existing_file", "aws_keypair")


class EnvConfigError(typer.Exit):
    """Typer.Exit(1) subclass — lets callers tell apart bail-from-validation
    vs. bail-from-AWS-failure if needed (today both surface as exit 1)."""

    def __init__(self) -> None:
        super().__init__(code=1)


def _fail(message: str, *, hint: str | None = None) -> "EnvConfigError":
    ui.error(message)
    if hint:
        ui.muted(hint)
    return EnvConfigError()


def build_infra_config_from_env(
    env: dict[str, str],
    *,
    auto_delete_cnames: bool | None = None,
) -> InfraConfig:
    """Validate `env` and return an `InfraConfig` ready for `TerraformRunner`.

    Side effects (intentional — these mirror the wizard):
    - validates AWS credentials via STS
    - saves the credential session to `~/.iblai-infra/session.json`
    - generates an SSH keypair on disk if `SSH_KEY_METHOD=generate`
    - deletes conflicting Route53 CNAMEs when ACM is in use and the
      operator allowed it (default true; override via
      `AUTO_DELETE_CONFLICTING_DNS=false` or the keyword arg)

    Single-server only. Raises `typer.Exit(1)` on any validation failure
    after printing a human-readable error.
    """
    # Single-server is the only deployment type this command supports.
    # Catch operators who copy a launch-env file expecting it to work.
    deployment_raw = (env.get("DEPLOYMENT_TYPE") or "single-server").strip().lower()
    if deployment_raw not in VALID_DEPLOYMENT_TYPES:
        raise _fail(
            f"DEPLOYMENT_TYPE={deployment_raw!r} is not supported by provision-env.",
            hint=(
                "Only 'single-server' is supported here. For multi-server / "
                "call-server, use the interactive [brand]iblai infra provision[/brand] "
                "wizard."
            ),
        )

    if auto_delete_cnames is None:
        auto_delete_cnames = parse_bool(
            env.get("AUTO_DELETE_CONFLICTING_DNS"), default=True
        )

    credentials = _build_credentials(env)

    # STS-validate before doing anything else — it's the cheapest
    # smoke test and gives the clearest error if creds are bad.
    try:
        identity = validate_credentials(credentials)
    except ValueError as exc:
        raise _fail(f"AWS credential check failed: {exc}")
    credentials.account_id = identity.account_id
    credentials.arn = identity.arn

    # Save session so subsequent `iblai infra setup <name>` can reuse it.
    from iblai_infra.terraform.state import save_session
    save_session(credentials)

    project_name, environment = _parse_project(env)
    network = _build_network(env)
    compute = _build_compute(env)
    dns, certificates = _build_dns_and_certs(
        env, credentials, auto_delete_cnames=auto_delete_cnames
    )
    ssh = _build_ssh(env, credentials, project_name, environment)
    waf = _build_waf(env)

    return InfraConfig(
        project_name=project_name,
        environment=environment,
        deployment_type=DeploymentType.SINGLE,
        credentials=credentials,
        network=network,
        compute=compute,
        ssh=ssh,
        certificates=certificates,
        dns=dns,
        waf=waf,
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_credentials(env: dict[str, str]) -> AWSCredentials:
    region = (env.get("AWS_DEFAULT_REGION") or "us-east-1").strip()
    access_key = (env.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = (env.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    profile = (env.get("AWS_PROFILE") or "").strip()

    if access_key and secret_key:
        return AWSCredentials(
            method=AuthMethod.ACCESS_KEY,
            access_key_id=access_key,
            secret_access_key=secret_key,
            region=region,
        )
    if profile:
        return AWSCredentials(
            method=AuthMethod.PROFILE,
            profile=profile,
            region=region,
        )
    raise _fail(
        "AWS credentials missing.",
        hint=(
            "Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or AWS_PROFILE, "
            "in your .env."
        ),
    )


def _parse_project(env: dict[str, str]) -> tuple[str, Environment]:
    missing = [k for k in REQUIRED_BASE_KEYS if not env.get(k)]
    if missing:
        ui.error("Missing required variables in .env:")
        for k in missing:
            ui.muted(f"  - {k}")
        raise EnvConfigError()

    project_name = env["PROJECT_NAME"].strip().lower()
    env_raw = (env.get("ENVIRONMENT") or "staging").strip().lower()
    try:
        environment = Environment(env_raw)
    except ValueError:
        raise _fail(
            f"ENVIRONMENT={env_raw!r} is invalid.",
            hint="Use one of: dev, staging, prod.",
        )
    return project_name, environment


def _build_network(env: dict[str, str]) -> NetworkConfig:
    vpc_cidr = (env.get("VPC_CIDR") or "10.0.0.0/16").strip()
    vpn_raw = env["VPN_IP"].strip()
    if vpn_raw.lower() == "auto":
        detected = detect_current_ip()
        if not detected:
            raise _fail(
                "VPN_IP=auto could not detect a public IP.",
                hint="Set VPN_IP to a literal IPv4 address.",
            )
        vpn_ip = detected
    else:
        vpn_ip = vpn_raw

    try:
        return NetworkConfig(vpc_cidr=vpc_cidr, vpn_ip=vpn_ip)
    except ValueError as exc:
        raise _fail(f"Invalid network configuration: {exc}")


def _build_compute(env: dict[str, str]) -> ComputeConfig:
    instance_type = (env.get("INSTANCE_TYPE") or "t3.2xlarge").strip()
    volume_type = (env.get("VOLUME_TYPE") or "gp3").strip()
    volume_raw = (env.get("VOLUME_SIZE") or "100").strip()
    try:
        volume_size = int(volume_raw)
    except ValueError:
        raise _fail(f"VOLUME_SIZE={volume_raw!r} is not an integer.")
    try:
        return ComputeConfig(
            instance_type=instance_type,
            volume_size=volume_size,
            volume_type=volume_type,
        )
    except ValueError as exc:
        raise _fail(f"Invalid compute configuration: {exc}")


def _build_ssh(
    env: dict[str, str],
    credentials: AWSCredentials,
    project_name: str,
    environment: Environment,
) -> SSHConfig:
    method_raw = (env.get("SSH_KEY_METHOD") or "generate").strip().lower()
    if method_raw not in VALID_SSH_METHODS:
        raise _fail(
            f"SSH_KEY_METHOD={method_raw!r} is invalid.",
            hint=f"Use one of: {', '.join(VALID_SSH_METHODS)}.",
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

    if method == SSHKeyMethod.EXISTING_FILE:
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
            raise _fail(
                "SSH_KEY_METHOD=existing_file requires SSH_PUBLIC_KEY_PATH or "
                "SSH_PUBLIC_KEY."
            )
        # Optional private key path so a follow-up `iblai infra setup` can find it.
        priv_raw = (env.get("SSH_PRIVATE_KEY_PATH") or "").strip()
        priv_path = Path(priv_raw).expanduser() if priv_raw else None
        return SSHConfig(
            method=method,
            key_name=key_name,
            public_key=public_key,
            private_key_path=priv_path,
        )

    # AWS_KEYPAIR: validate it exists in the target account/region.
    key_name = (env.get("SSH_KEY_NAME") or "").strip()
    if not key_name:
        raise _fail("SSH_KEY_METHOD=aws_keypair requires SSH_KEY_NAME.")
    session = get_session(credentials)
    available = [kp.name for kp in list_key_pairs(session)]
    if key_name not in available:
        raise _fail(
            f"SSH_KEY_NAME={key_name!r} not found in {credentials.region}.",
            hint=(
                f"Available key pairs: {', '.join(available) or '(none)'}"
            ),
        )
    return SSHConfig(method=method, key_name=key_name)


def _build_dns_and_certs(
    env: dict[str, str],
    credentials: AWSCredentials,
    *,
    auto_delete_cnames: bool,
) -> tuple[DNSConfig, CertificateConfig]:
    base_domain = env["DOMAIN"].strip().lower()
    method_raw = (env.get("CERT_METHOD") or "auto").strip().lower()
    if method_raw not in VALID_CERT_METHODS:
        raise _fail(
            f"CERT_METHOD={method_raw!r} is invalid.",
            hint=f"Use one of: {', '.join(VALID_CERT_METHODS)}.",
        )

    explicit_zone_id = (env.get("HOSTED_ZONE_ID") or "").strip() or None

    # 'auto' / 'acm' both need to look at Route53 — share the lookup.
    matching_zones = []
    if method_raw in ("auto", "acm"):
        session = get_session(credentials)
        zones = list_hosted_zones(session)
        matching_zones = [
            z for z in zones if base_domain.endswith(z.name) or z.name == base_domain
        ]

    # Resolve the effective method.
    if method_raw == "auto":
        effective_method = "acm" if matching_zones else "none"
        if effective_method == "none":
            ui.warning(
                f"No Route53 hosted zone matches {base_domain} — falling back to "
                "HTTP-only. Set CERT_METHOD=upload or supply DNS yourself."
            )
    else:
        effective_method = method_raw

    if effective_method == "acm":
        zone_id = _resolve_zone_id(matching_zones, explicit_zone_id, base_domain)
        if zone_id is None:
            raise _fail(
                f"CERT_METHOD=acm but no Route53 zone matches {base_domain}.",
                hint=(
                    "Set HOSTED_ZONE_ID explicitly, switch to CERT_METHOD=auto, "
                    "or use CERT_METHOD=upload/none."
                ),
            )
        _handle_dns_conflicts(
            credentials, zone_id, base_domain,
            auto_delete=auto_delete_cnames,
        )
        return (
            DNSConfig(
                base_domain=base_domain,
                use_route53=True,
                hosted_zone_id=zone_id,
            ),
            CertificateConfig(method=CertMethod.ACM, hosted_zone_id=zone_id),
        )

    if effective_method == "upload":
        cert = _load_uploaded_cert(env)
        return (
            DNSConfig(base_domain=base_domain, use_route53=False),
            cert,
        )

    # 'none'
    return (
        DNSConfig(base_domain=base_domain, use_route53=False),
        CertificateConfig(method=CertMethod.NONE),
    )


def _resolve_zone_id(
    matching_zones: list,
    explicit_zone_id: str | None,
    base_domain: str,
) -> str | None:
    if not matching_zones:
        return explicit_zone_id  # may still be None
    if explicit_zone_id:
        # Trust the operator's pick if it's in the matching set.
        for z in matching_zones:
            if z.zone_id == explicit_zone_id:
                return z.zone_id
        # Explicit ID didn't match what Route53 returned — surface clearly.
        raise _fail(
            f"HOSTED_ZONE_ID={explicit_zone_id!r} does not match any Route53 zone "
            f"covering {base_domain}.",
        )
    if len(matching_zones) > 1:
        raise _fail(
            f"Multiple Route53 zones match {base_domain}: "
            f"{', '.join(z.zone_id for z in matching_zones)}.",
            hint="Disambiguate by setting HOSTED_ZONE_ID in .env.",
        )
    return matching_zones[0].zone_id


def _handle_dns_conflicts(
    credentials: AWSCredentials,
    zone_id: str,
    base_domain: str,
    *,
    auto_delete: bool,
) -> None:
    session = get_session(credentials)
    subdomains = [s.format(domain=base_domain) for s in IBL_SUBDOMAINS]
    conflicts = find_conflicting_records(session, zone_id, subdomains)
    if not conflicts:
        return
    if not auto_delete:
        ui.error(
            f"Found {len(conflicts)} conflicting CNAME record(s) in zone {zone_id}:"
        )
        for c in conflicts:
            values = [rr["Value"] for rr in c.get("ResourceRecords", [])]
            ui.muted(f"  CNAME  {c['Name'].rstrip('.')}  →  {', '.join(values)}")
        ui.muted(
            "Set AUTO_DELETE_CONFLICTING_DNS=true (or remove the records "
            "manually) and retry."
        )
        raise EnvConfigError()
    ui.warning(
        f"Removing {len(conflicts)} conflicting CNAME record(s) in zone {zone_id}"
    )
    delete_route53_records(session, zone_id, conflicts)


def _build_waf(env: dict[str, str]) -> WAFConfig:
    """Parse ENABLE_WAF + WAF_ALLOWED_IPS into a WAFConfig.

    WAF_ALLOWED_IPS is required when ENABLE_WAF=true. Accepts bare IPs and
    CIDR; the model's validator handles /32 normalisation and rejects
    invalid tokens with a clear error.
    """
    enabled = parse_bool(env.get("ENABLE_WAF"), default=False)
    raw_ips = (env.get("WAF_ALLOWED_IPS") or "").strip()
    tokens = [t.strip() for t in raw_ips.split(",") if t.strip()]
    try:
        return WAFConfig(enabled=enabled, allowed_ips=tokens)
    except ValueError as exc:
        raise _fail(
            f"Invalid WAF configuration: {exc}",
            hint=(
                "When ENABLE_WAF=true, set WAF_ALLOWED_IPS to a comma-separated "
                "list of IPs/CIDRs (e.g. 203.0.113.7,10.0.0.0/16)."
            ),
        )


def _load_uploaded_cert(env: dict[str, str]) -> CertificateConfig:
    body_path = (env.get("CERT_BODY_PATH") or "").strip()
    key_path = (env.get("CERT_KEY_PATH") or "").strip()
    chain_path = (env.get("CERT_CHAIN_PATH") or "").strip()
    missing = []
    if not body_path:
        missing.append("CERT_BODY_PATH")
    if not key_path:
        missing.append("CERT_KEY_PATH")
    if missing:
        raise _fail(
            "CERT_METHOD=upload requires: " + ", ".join(missing),
        )
    body_p = Path(body_path).expanduser()
    key_p = Path(key_path).expanduser()
    if not body_p.exists():
        raise _fail(f"CERT_BODY_PATH not found: {body_p}")
    if not key_p.exists():
        raise _fail(f"CERT_KEY_PATH not found: {key_p}")
    chain_text = None
    if chain_path:
        chain_p = Path(chain_path).expanduser()
        if not chain_p.exists():
            raise _fail(f"CERT_CHAIN_PATH not found: {chain_p}")
        chain_text = chain_p.read_text()
    return CertificateConfig(
        method=CertMethod.UPLOAD,
        cert_body=body_p.read_text(),
        cert_private_key=key_p.read_text(),
        cert_chain=chain_text,
    )
