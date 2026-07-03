"""Step 4 — Domain, DNS, and Certificate configuration."""

from __future__ import annotations

from pathlib import Path

import questionary

from iblai_infra import ui
from iblai_infra.models import (
    AWSCredentials,
    CertificateConfig,
    CertMethod,
    DNSConfig,
    IBL_SUBDOMAINS,
    WAFConfig,
)
from iblai_infra.providers.aws import (
    delete_route53_records,
    find_conflicting_records,
    get_session,
    list_hosted_zones,
)

TOTAL_STEPS = 5


def prompt_dns_and_certs(
    credentials: AWSCredentials,
    is_call_server: bool = False,
) -> tuple[DNSConfig, CertificateConfig]:
    """Prompt for domain, DNS provider, and certificate source.

    When ``is_call_server`` is True, skips the 19-subdomain expansion + CNAME
    conflict check — the call-server Terraform template only creates a single
    A record for the call FQDN, and TLS is terminated inside LiveKit rather
    than at an AWS ALB.
    """

    ui.step_header(4, TOTAL_STEPS, "Domain & Certificates")

    # For call-server the CLI's `ibl call` auto-prepends "call." to BASE_DOMAIN
    # when generating the LiveKit WS URL. Prompting with "call.example.com"
    # here used to cause operators to set BASE_DOMAIN=call.<root> which then
    # produced wss://call.call.<root>. Ask for the PARENT domain instead.
    prompt_text = (
        "Call server base domain (WS URL will be wss://call.<this>, e.g. 'iblai.org'):"
        if is_call_server
        else "Base domain:"
    )

    # ----- base domain -----
    base_domain = questionary.text(
        prompt_text,
        validate=lambda v: _validate_domain(v) or "Enter a valid domain (e.g. example.com)",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if base_domain is None:
        ui.abort()
    base_domain = base_domain.strip().lower()

    # ----- Route53 check -----
    session = get_session(credentials)
    zones = list_hosted_zones(session)

    # Find zones matching the domain
    matching_zones = [z for z in zones if base_domain.endswith(z.name) or z.name == base_domain]

    # Call-server has a much simpler path: optional R53 A-record, no ACM certs
    # (LiveKit terminates TLS internally), no subdomain expansion.
    if is_call_server:
        hosted_zone_id: str | None = None
        if matching_zones:
            if len(matching_zones) == 1:
                zone = matching_zones[0]
                ui.info(f"Found Route53 zone: [highlight]{zone.name}[/highlight] ({zone.zone_id})")
            else:
                zone = questionary.select(
                    "Select hosted zone:",
                    choices=[
                        questionary.Choice(f"{z.name} ({z.zone_id})", value=z)
                        for z in matching_zones
                    ],
                    style=ui.PROMPT_STYLE,
                    qmark=ui.QMARK,
                ).ask()
                if zone is None:
                    ui.abort()
            use_r53 = questionary.confirm(
                f"Create a Route53 A record for {base_domain} → Elastic IP?",
                default=True,
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if use_r53 is None:
                ui.abort()
            if use_r53:
                hosted_zone_id = zone.zone_id
        else:
            ui.muted(f"No Route53 hosted zone in this account matches {base_domain}.")
            ui.muted("Skipping DNS — point your A record at the Elastic IP after provisioning.")

        return (
            DNSConfig(
                base_domain=base_domain,
                use_route53=bool(hosted_zone_id),
                hosted_zone_id=hosted_zone_id,
            ),
            CertificateConfig(
                method=CertMethod.ACM if hosted_zone_id else CertMethod.NONE,
                hosted_zone_id=hosted_zone_id,
            ),
        )

    if matching_zones:
        ui.success(f"Found Route53 hosted zone(s) matching [highlight]{base_domain}[/highlight]")

        use_route53 = questionary.select(
            "DNS & Certificate strategy:",
            choices=[
                questionary.Choice(
                    "Use Route53 + ACM (auto-managed DNS & certificates)",
                    value="route53",
                ),
                questionary.Choice(
                    "Upload my own certificate files",
                    value="upload",
                ),
                questionary.Choice(
                    "Skip HTTPS for now (HTTP only)",
                    value="none",
                ),
            ],
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
    ).ask()
        if use_route53 is None:
            ui.abort()
    else:
        if zones:
            ui.muted(f"No Route53 hosted zone found for {base_domain}")
            ui.muted(f"Available zones: {', '.join(z.name for z in zones)}")
        else:
            ui.muted("No Route53 hosted zones found in this account")

        use_route53 = questionary.select(
            "Certificate strategy:",
            choices=[
                questionary.Choice(
                    "Upload my own certificate files (PEM format)",
                    value="upload",
                ),
                questionary.Choice(
                    "Skip HTTPS for now (HTTP only — ALB will listen on port 80)",
                    value="none",
                ),
            ],
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
    ).ask()
        if use_route53 is None:
            ui.abort()

    # ----- handle each path -----
    hosted_zone_id = None
    cert_config: CertificateConfig

    if use_route53 == "route53":
        # Let user pick the zone if multiple
        if len(matching_zones) == 1:
            zone = matching_zones[0]
            ui.info(f"Using zone: [highlight]{zone.name}[/highlight] ({zone.zone_id})")
        else:
            zone_selection = questionary.select(
                "Select hosted zone:",
                choices=[
                    questionary.Choice(
                        f"{z.name} ({z.zone_id}, {z.record_count} records)",
                        value=z,
                    )
                    for z in matching_zones
                ],
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
    ).ask()
            if zone_selection is None:
                ui.abort()
            zone = zone_selection

        hosted_zone_id = zone.zone_id

        # Show subdomains that will be created
        subdomains = [s.format(domain=base_domain) for s in IBL_SUBDOMAINS]
        ui.newline()
        ui.info("The following DNS A-records will be created (aliased to the ALB):")
        for sd in subdomains:
            ui.muted(f"  {sd}")

        # Check for conflicting CNAME records that would block A record creation
        ui.newline()
        from rich.status import Status
        with Status("  [info]Checking for conflicting DNS records...[/info]", console=ui.console):
            conflicts = find_conflicting_records(session, hosted_zone_id, subdomains)

        if conflicts:
            ui.warning(
                f"Found {len(conflicts)} existing CNAME record(s) that conflict with the "
                "A records Terraform will create:"
            )
            for c in conflicts:
                values = [rr["Value"] for rr in c.get("ResourceRecords", [])]
                ui.muted(f"  CNAME  {c['Name'].rstrip('.')}  →  {', '.join(values)}")

            ui.newline()
            ui.warning(
                "These CNAME records must be deleted before A records can be created. "
                "DNS does not allow both types for the same name."
            )
            delete_confirm = questionary.confirm(
                "Delete these conflicting CNAME records and proceed?",
                default=True,
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if not delete_confirm:
                ui.abort("Aborted — no DNS changes made.")

            with Status("  [info]Removing conflicting CNAME records...[/info]", console=ui.console):
                delete_route53_records(session, hosted_zone_id, conflicts)
            ui.success(f"Removed {len(conflicts)} conflicting CNAME record(s)")
        else:
            ui.success("No conflicting DNS records found")

        confirm_dns = questionary.confirm(
            "Proceed with creating these DNS records?",
            default=True,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if not confirm_dns:
            ui.abort("Aborted — no DNS records will be created.")

        cert_config = CertificateConfig(
            method=CertMethod.ACM,
            hosted_zone_id=hosted_zone_id,
        )

    elif use_route53 == "upload":
        cert_config = _prompt_cert_upload()

    else:  # none
        ui.newline()
        ui.warning("ALB will only have an HTTP listener (port 80)")
        proceed = questionary.confirm(
            "Proceed without HTTPS?",
            default=False,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
    ).ask()
        if not proceed:
            ui.abort("Aborted — please prepare certificate files and try again.")

        cert_config = CertificateConfig(method=CertMethod.NONE)

    dns_config = DNSConfig(
        base_domain=base_domain,
        use_route53=(use_route53 == "route53"),
        hosted_zone_id=hosted_zone_id,
    )

    return dns_config, cert_config


# ---------------------------------------------------------------------------
# GCP DNS & certificate wizard (Cloud DNS + Google-managed cert)
# ---------------------------------------------------------------------------

def prompt_gcp_dns_and_certs(gcp_credentials) -> tuple[DNSConfig, CertificateConfig]:
    """Prompt for domain, Cloud DNS zone, and certificate source (GCP)."""
    ui.step_header(4, TOTAL_STEPS, "Domain & Certificates")

    base_domain = questionary.text(
        "Base domain:",
        validate=lambda v: _validate_domain(v) or "Enter a valid domain (e.g. example.com)",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if base_domain is None:
        ui.abort()
    base_domain = base_domain.strip().lower()

    from iblai_infra.providers import gcp as gcp_provider

    from rich.status import Status
    with Status("  [info]Looking up Cloud DNS zones...[/info]", console=ui.console):
        zones = gcp_provider.list_managed_zones(gcp_credentials)
    matching = [
        z for z in zones if base_domain == z.dns_name or base_domain.endswith("." + z.dns_name)
    ]

    if matching:
        ui.success(f"Found Cloud DNS zone(s) matching [highlight]{base_domain}[/highlight]")
        strategy = questionary.select(
            "DNS & Certificate strategy:",
            choices=[
                questionary.Choice("Use Cloud DNS + Google-managed certificate", value="managed"),
                questionary.Choice("Upload my own certificate files", value="upload"),
                questionary.Choice("Skip HTTPS for now (HTTP only)", value="none"),
            ],
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
    else:
        ui.muted(f"No Cloud DNS zone in this project matches {base_domain}")
        strategy = questionary.select(
            "DNS & Certificate strategy:",
            choices=[
                questionary.Choice("Create a Cloud DNS zone + Google-managed certificate", value="create"),
                questionary.Choice("Upload my own certificate files", value="upload"),
                questionary.Choice("Skip HTTPS for now (HTTP only)", value="none"),
            ],
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
    if strategy is None:
        ui.abort()

    if strategy == "managed":
        if len(matching) == 1:
            zone = matching[0]
            ui.info(f"Using zone: [highlight]{zone.name}[/highlight] ({zone.dns_name})")
        else:
            zone = questionary.select(
                "Select Cloud DNS zone:",
                choices=[questionary.Choice(f"{z.name} ({z.dns_name})", value=z) for z in matching],
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if zone is None:
                ui.abort()
        zone_name = zone.name

        subdomains = [base_domain] + [s.format(domain=base_domain) for s in IBL_SUBDOMAINS]
        ui.newline()
        ui.info("A records will be created for the base domain + 19 subdomains, pointing at the LB IP.")
        with Status("  [info]Checking for conflicting DNS records...[/info]", console=ui.console):
            conflicts = gcp_provider.find_conflicting_records(gcp_credentials, zone_name, subdomains)
        if conflicts:
            ui.warning(f"Found {len(conflicts)} conflicting A/CNAME record(s):")
            for c in conflicts:
                ui.muted(f"  {c.record_type}  {c.name.rstrip('.')}  ->  {', '.join(c.rrdatas)}")
            delete_confirm = questionary.confirm(
                "Delete these conflicting records and proceed?",
                default=True,
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
            ).ask()
            if not delete_confirm:
                ui.abort("Aborted — no DNS changes made.")
            with Status("  [info]Removing conflicting records...[/info]", console=ui.console):
                gcp_provider.delete_records(gcp_credentials, zone_name, conflicts)
            ui.success(f"Removed {len(conflicts)} conflicting record(s)")
        else:
            ui.success("No conflicting DNS records found")

        ui.newline()
        ui.muted("Note: the Google-managed certificate provisions asynchronously —")
        ui.muted("HTTPS can take 10-60 minutes to go live after DNS resolves to the LB.")
        return (
            DNSConfig(base_domain=base_domain, dns_zone_name=zone_name, create_dns_zone=False),
            CertificateConfig(method=CertMethod.MANAGED),
        )

    if strategy == "create":
        zone_name = questionary.text(
            "New Cloud DNS zone name (resource name, e.g. my-zone):",
            validate=lambda v: bool(v.strip()) and v.strip().replace("-", "").isalnum()
            or "Lowercase letters, numbers, and hyphens",
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if zone_name is None:
            ui.abort()
        ui.newline()
        ui.warning("After provisioning, delegate the printed nameservers at your registrar.")
        ui.muted("The managed certificate can only validate once delegation is live.")
        return (
            DNSConfig(base_domain=base_domain, dns_zone_name=zone_name.strip(), create_dns_zone=True),
            CertificateConfig(method=CertMethod.MANAGED),
        )

    if strategy == "upload":
        return DNSConfig(base_domain=base_domain), _prompt_cert_upload()

    # none
    ui.newline()
    ui.warning("The load balancer will only serve HTTP (port 80).")
    proceed = questionary.confirm(
        "Proceed without HTTPS?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if not proceed:
        ui.abort("Aborted — prepare a domain/certificate and try again.")
    return DNSConfig(base_domain=base_domain), CertificateConfig(method=CertMethod.NONE)


# ---------------------------------------------------------------------------
# Certificate upload sub-flow
# ---------------------------------------------------------------------------

def _prompt_cert_upload() -> CertificateConfig:
    """Prompt for certificate file paths and read their contents."""

    ui.newline()
    ui.info("Provide PEM-encoded certificate files for ALB HTTPS termination")
    ui.newline()

    cert_path = questionary.path(
        "Certificate file (.pem):",
        validate=lambda p: (
            Path(p).expanduser().exists() or "File not found"
        ),
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if cert_path is None:
        ui.abort()

    key_path = questionary.path(
        "Private key file (.pem):",
        validate=lambda p: (
            Path(p).expanduser().exists() or "File not found"
        ),
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if key_path is None:
        ui.abort()

    chain_path = questionary.text(
        "Certificate chain file (.pem) [optional, press Enter to skip]:",
        default="",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    cert_body = Path(cert_path).expanduser().read_text()
    cert_key = Path(key_path).expanduser().read_text()

    cert_chain = None
    if chain_path and chain_path.strip():
        chain_p = Path(chain_path).expanduser()
        if chain_p.exists():
            cert_chain = chain_p.read_text()
        else:
            ui.warning(f"Chain file not found: {chain_path} — skipping")

    ui.success("Certificate files loaded")

    return CertificateConfig(
        method=CertMethod.UPLOAD,
        cert_body=cert_body,
        cert_private_key=cert_key,
        cert_chain=cert_chain,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_domain(value: str) -> bool:
    """Basic domain name validation."""
    v = value.strip().lower()
    if not v or "." not in v:
        return False
    parts = v.split(".")
    return all(
        part and part.replace("-", "").isalnum()
        for part in parts
    )


# ---------------------------------------------------------------------------
# WAF sub-flow (called from the wizard after DNS/certs, single-server only)
# ---------------------------------------------------------------------------


def _validate_ip_or_cidr(token: str) -> bool:
    """Return True if ``token`` parses as an IPv4 address or CIDR network."""
    import ipaddress
    s = (token or "").strip()
    if not s:
        return False
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        pass
    try:
        ipaddress.ip_network(s, strict=False)
        return True
    except ValueError:
        return False


def _validate_ip_csv(value: str) -> bool | str:
    """questionary-compatible validator for a comma-separated IP/CIDR list."""
    s = (value or "").strip()
    if not s:
        return "Provide at least one IP or CIDR"
    tokens = [t.strip() for t in s.split(",") if t.strip()]
    if not tokens:
        return "Provide at least one IP or CIDR"
    bad = [t for t in tokens if not _validate_ip_or_cidr(t)]
    if bad:
        return f"Invalid IP/CIDR: {', '.join(bad)}"
    return True


def _prompt_waf_ips(default: list[str] | None = None) -> list[str]:
    """Prompt for the WAF admin allowlist (comma-separated IPs/CIDRs).

    Pre-fills the prompt with ``default`` (joined as comma-separated) when
    provided — used by the post-provision ``iblai infra waf enable`` flow
    to let the operator edit the existing list. Returns the raw token list
    (caller wraps it in ``WAFConfig`` so validator-normalisation runs).
    """
    pre = ", ".join(default or [])
    raw = questionary.text(
        "Admin IPs/CIDRs (comma-separated, e.g. 203.0.113.7,10.0.0.0/16):",
        default=pre,
        validate=_validate_ip_csv,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if raw is None:
        ui.abort()
    return [t.strip() for t in raw.split(",") if t.strip()]


def prompt_waf(base_domain: str) -> WAFConfig:
    """Optional WAFv2 prompt — gates an admin IP allowlist on the ALB.

    Called from the wizard after DNS/certs for single-server deployments
    only. Default is to skip; on opt-in, requires at least one IP or CIDR
    for the admin allowlist (Swagger, Studio, /admin, /data).
    """
    ui.newline()
    ui.console.rule("[brand]WAF (optional)[/brand]")
    ui.info(
        "Attaches an AWS WAFv2 Web ACL to the ALB. Allow-list IPs reach "
        "admin surfaces (Swagger UI, edX Studio, /admin/, DM /data); "
        "everyone else is blocked from those. learn." + base_domain +
        " and apps.learn." + base_domain + " stay public."
    )
    ui.muted(
        "  Includes AWS managed rule groups (Common, SQLi, KnownBadInputs, "
        "IpReputation, WordPress, PHP) and a .git/.env path-traversal block."
    )

    enabled = questionary.confirm(
        "Enable AWS WAFv2 protection?",
        default=False,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if enabled is None:
        ui.abort()

    if not enabled:
        ui.success("WAF: [highlight]Skip[/highlight]")
        return WAFConfig(enabled=False)

    tokens = _prompt_waf_ips()
    try:
        cfg = WAFConfig(enabled=True, allowed_ips=tokens)
    except ValueError as exc:
        ui.error(str(exc))
        ui.abort()

    ui.success(
        f"WAF: [highlight]Enabled[/highlight] "
        f"({len(cfg.allowed_ips)} admin IP/CIDR)"
    )
    return cfg
