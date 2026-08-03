"""DNS and certificate verification for a provisioned environment.

Provisioning creates the load balancer but, on every path except Route53+ACM
and an existing Cloud DNS zone, the operator has to create the DNS records
themselves - often by asking a third party who manages the domain. Until those
records resolve, nothing is reachable and no certificate can validate, and the
failure shows up much later as a confusing platform error.

This module answers three questions for a given environment:

  1. Does each platform subdomain resolve at all?
  2. Does it resolve to *this* deployment's load balancer, or to something else?
  3. What state is the certificate in?

Lookups go to public resolvers rather than the system one, so a stale local
cache or split-horizon DNS cannot report success while the rest of the world
still sees nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from iblai_infra.models import CertMethod, CloudProvider, DeploymentType, InfraConfig

# Public resolvers, queried instead of the system resolver on purpose.
PUBLIC_RESOLVERS = ["8.8.8.8", "1.1.1.1"]
RESOLVE_TIMEOUT = 5.0


class RecordStatus(str, Enum):
    OK = "ok"            # resolves to this deployment's load balancer
    WRONG = "wrong"      # resolves, but somewhere else
    MISSING = "missing"  # does not resolve


@dataclass
class RecordResult:
    name: str
    status: RecordStatus
    resolved: list[str] = field(default_factory=list)


@dataclass
class DNSReport:
    records: list[RecordResult]
    target: str                      # what the records should point at
    target_ips: list[str]            # resolved to addresses for comparison
    cert_status: str | None = None   # provider-reported certificate state
    resolver_error: str | None = None

    @property
    def ok(self) -> list[RecordResult]:
        return [r for r in self.records if r.status is RecordStatus.OK]

    @property
    def problems(self) -> list[RecordResult]:
        return [r for r in self.records if r.status is not RecordStatus.OK]

    @property
    def all_ok(self) -> bool:
        return bool(self.records) and not self.problems

    def summary(self) -> str:
        return f"{len(self.ok)}/{len(self.records)} resolving to the load balancer"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolver():
    """Return a dnspython resolver pointed at the public resolvers.

    Raises ImportError with an actionable message when dnspython is absent.
    """
    try:
        import dns.resolver
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ImportError(
            "dnspython is required for DNS verification. Install it with "
            "`uv sync` (it ships as a dependency of this CLI)."
        ) from exc

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = list(PUBLIC_RESOLVERS)
    resolver.timeout = RESOLVE_TIMEOUT
    resolver.lifetime = RESOLVE_TIMEOUT
    return resolver


def resolve_addresses(name: str) -> list[str]:
    """Resolve ``name`` to IPv4 addresses, following CNAMEs.

    Returns an empty list when the name does not resolve. Any resolver failure
    is treated as "does not resolve" - the caller reports it as MISSING, which
    is what the operator needs to act on either way.
    """
    import dns.resolver

    try:
        answer = _resolver().resolve(name, "A", raise_on_no_answer=False)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except Exception:
        return []
    if answer is None or answer.rrset is None:
        return []
    return sorted(str(r) for r in answer)


# ---------------------------------------------------------------------------
# What the records should point at
# ---------------------------------------------------------------------------

def expected_target(config: InfraConfig, outputs: dict) -> str:
    """The address or hostname every platform record should resolve to."""
    if config.deployment_type == DeploymentType.CALL:
        return outputs.get("elastic_ip") or outputs.get("instance_public_ip") or ""
    if config.cloud == CloudProvider.GCP:
        return outputs.get("lb_ip_address") or ""
    return outputs.get("alb_dns_name") or ""


def check_dns(config: InfraConfig, outputs: dict) -> DNSReport:
    """Resolve every platform subdomain and compare against the load balancer."""
    target = expected_target(config, outputs)

    # A literal address needs no lookup; an ALB hostname does.
    target_ips: list[str] = []
    if target:
        if target.replace(".", "").isdigit():
            target_ips = [target]
        else:
            target_ips = resolve_addresses(target)

    if config.deployment_type == DeploymentType.CALL:
        names = [config.dns.base_domain]
    else:
        names = list(config.dns.subdomains)

    expected = set(target_ips)
    records: list[RecordResult] = []
    for name in names:
        got = resolve_addresses(name)
        if not got:
            status = RecordStatus.MISSING
        elif not expected or (expected & set(got)):
            # No comparable target (e.g. the ALB itself did not resolve) means
            # we can only report that the name resolves - better than nothing,
            # and not a false WRONG.
            status = RecordStatus.OK
        else:
            status = RecordStatus.WRONG
        records.append(RecordResult(name=name, status=status, resolved=got))

    return DNSReport(records=records, target=target, target_ips=target_ips)


# ---------------------------------------------------------------------------
# Certificate state
# ---------------------------------------------------------------------------

def certificate_status(config: InfraConfig, outputs: dict) -> str | None:
    """Best-effort certificate state, or None when it cannot be determined.

    Never raises: this is diagnostic colour on top of the DNS result, and a
    missing SDK or permission must not fail the check.
    """
    method = config.certificates.method
    if method in (CertMethod.NONE, CertMethod.UPLOAD):
        return None

    try:
        if config.cloud == CloudProvider.GCP:
            return _gcp_certificate_status(config, outputs)
        return _acm_certificate_status(config, outputs)
    except Exception:
        return None


def _acm_certificate_status(config: InfraConfig, outputs: dict) -> str | None:
    arns = [outputs.get("certificate_arn_1"), outputs.get("certificate_arn_2")]
    arns = [a for a in arns if a]
    if not arns:
        return None

    from iblai_infra.providers.aws import get_session

    acm = get_session(config.credentials).client("acm")
    states = []
    for arn in arns:
        detail = acm.describe_certificate(CertificateArn=arn)
        states.append(detail["Certificate"]["Status"])
    # Report the least-progressed state - that is the one still blocking.
    for blocking in ("PENDING_VALIDATION", "FAILED", "VALIDATION_TIMED_OUT"):
        if blocking in states:
            return blocking
    return states[0] if states else None


def _gcp_certificate_status(config: InfraConfig, outputs: dict) -> str | None:
    name = outputs.get("certificate_name")
    if not name:
        return None

    from google.cloud import compute_v1  # type: ignore

    from iblai_infra.providers.gcp import _scoped_credentials

    creds, project = _scoped_credentials(config.gcp_credentials)
    client = compute_v1.SslCertificatesClient(credentials=creds)
    cert = client.get(project=project, ssl_certificate=name)
    managed = getattr(cert, "managed", None)
    return getattr(managed, "status", None) if managed else None


def build_report(config: InfraConfig, outputs: dict) -> DNSReport:
    """Full verification: DNS resolution plus certificate state."""
    report = check_dns(config, outputs)
    report.cert_status = certificate_status(config, outputs)
    return report
