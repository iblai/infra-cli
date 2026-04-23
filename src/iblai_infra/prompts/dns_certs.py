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

    prompt_text = "Call server FQDN (e.g. call.example.com):" if is_call_server else "Base domain:"

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
