"""GCP provider helpers - thin wrappers around the Google SDKs.

Mirrors the surface of ``providers/aws.py`` (credential validation, DNS zone
discovery, conflicting-record cleanup, permission checks) so the CLI/env flows
branch with minimal churn.

The Google libraries live in the optional ``[gcp]`` extra; this module imports
lazily and raises a friendly error if they're missing, so an AWS-only install
can still import it (e.g. for tests that patch it).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from iblai_infra.models import GCPAuthMethod, GCPCredentials

try:  # optional [gcp] extra
    import google.auth
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.cloud import dns
    from google.oauth2 import service_account

    _GCP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _GCP_AVAILABLE = False


CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _require_gcp() -> None:
    if not _GCP_AVAILABLE:
        raise RuntimeError(
            "GCP support requires extra dependencies. Install with:\n"
            "  pip install 'iblai-infra[gcp]'   (or: uv sync --extra gcp)"
        )


def is_available() -> bool:
    """True when the optional ``[gcp]`` dependencies are importable.

    Entry points call this up-front to fail with a clean 'install the extra'
    message instead of a traceback deep inside credential resolution.
    """
    return _GCP_AVAILABLE


# ---------------------------------------------------------------------------
# Data classes for return values (shapes mirror providers/aws.py)
# ---------------------------------------------------------------------------

@dataclass
class GCPCallerIdentity:
    account: str
    project_id: str


@dataclass
class ManagedZone:
    name: str          # zone resource name, e.g. "my-zone"
    dns_name: str      # domain without trailing dot, e.g. "example.com"
    visibility: str = "public"


@dataclass
class GCPPermissionResult:
    service: str
    description: str
    passed: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _scoped_credentials(gc: GCPCredentials):
    """Resolve google credentials + project for ``gc``. Returns (creds, project).

    Supports a service-account key JSON or Application Default Credentials. User
    credentials are pinned to a quota/billing project so project-scoped APIs work.
    """
    _require_gcp()
    if gc.method == GCPAuthMethod.SERVICE_ACCOUNT_KEY:
        if not gc.credentials_file:
            raise ValueError("service_account_key auth requires credentials_file")
        credentials = service_account.Credentials.from_service_account_file(
            gc.credentials_file, scopes=[CLOUD_PLATFORM_SCOPE],
        )
        project = gc.project_id or getattr(credentials, "project_id", None)
    else:  # ADC
        try:
            credentials, adc_project = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        except Exception as e:
            raise ValueError(
                "No Application Default Credentials found. Run "
                "`gcloud auth application-default login`, or use a service-account "
                f"key (auth method = service_account_key). ({e})"
            )
        project = gc.project_id or adc_project

    if not project:
        raise ValueError("Could not determine GCP project_id")

    if hasattr(credentials, "with_quota_project"):
        try:
            credentials = credentials.with_quota_project(project)
        except Exception:
            pass
    return credentials, project


def validate_credentials(gc: GCPCredentials) -> GCPCallerIdentity:
    """Confirm the credentials authenticate. Raises ValueError on failure.

    Deliberately API-light: mints/uses a token and reads the caller identity
    without calling a project API (Cloud Resource Manager may be disabled), so
    it never conflates 'bad credentials' with 'API not enabled'.
    """
    _require_gcp()
    try:
        credentials, project = _scoped_credentials(gc)
        if not getattr(credentials, "valid", False):
            credentials.refresh(Request())
        account = _identity(gc, credentials)
        return GCPCallerIdentity(account=account or "(unknown)", project_id=project)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"GCP authentication failed: {e}")


def _identity(gc: GCPCredentials, credentials) -> str | None:
    """Best-effort caller email: from the SA key, else the userinfo endpoint."""
    email = getattr(credentials, "service_account_email", None)
    if email and email != "default":
        return email
    if gc.method == GCPAuthMethod.SERVICE_ACCOUNT_KEY and gc.credentials_file:
        try:
            with open(gc.credentials_file) as fh:
                return json.load(fh).get("client_email")
        except Exception:
            pass
    try:
        session = AuthorizedSession(credentials)
        resp = session.get("https://www.googleapis.com/oauth2/v3/userinfo", timeout=10)
        if resp.status_code == 200:
            return resp.json().get("email")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Cloud DNS discovery + conflict handling
# ---------------------------------------------------------------------------

def list_managed_zones(gc: GCPCredentials) -> list[ManagedZone]:
    """List public Cloud DNS managed zones. Returns [] on any error."""
    _require_gcp()
    try:
        credentials, project = _scoped_credentials(gc)
        client = dns.Client(project=project, credentials=credentials)
        zones = [
            ManagedZone(
                name=z.name,
                dns_name=(z.dns_name or "").rstrip("."),
                visibility=getattr(z, "visibility", "public") or "public",
            )
            for z in client.list_zones()
        ]
        return [z for z in zones if z.visibility != "private"]
    except Exception:
        return []


def find_conflicting_records(
    gc: GCPCredentials, zone_name: str, subdomains: list[str]
) -> list:
    """Find existing A/CNAME records that collide with the A records we'd create."""
    _require_gcp()
    credentials, project = _scoped_credentials(gc)
    client = dns.Client(project=project, credentials=credentials)
    zone = client.zone(zone_name)
    targets = {sd.rstrip(".") + "." for sd in subdomains}
    return [
        rrs
        for rrs in zone.list_resource_record_sets()
        if rrs.name in targets and rrs.record_type in ("A", "CNAME")
    ]


def delete_records(gc: GCPCredentials, zone_name: str, records: list) -> None:
    """Delete the given Cloud DNS record sets."""
    _require_gcp()
    if not records:
        return
    credentials, project = _scoped_credentials(gc)
    client = dns.Client(project=project, credentials=credentials)
    zone = client.zone(zone_name)
    changes = zone.changes()
    for rrs in records:
        changes.delete_record_set(rrs)
    changes.create()


# ---------------------------------------------------------------------------
# Permission / API checks
# ---------------------------------------------------------------------------

# Minimum IAM roles + APIs required for provisioning (surfaced by
# `iblai infra permissions`). Analogous to aws.REQUIRED_IAM_POLICY.
REQUIRED_GCP_ROLES: dict[str, list[str]] = {
    "roles": [
        "roles/compute.admin",          # VPC, subnet, firewall, VM, LB, certs
        "roles/dns.admin",              # Cloud DNS zones + records
        "roles/iam.serviceAccountUser",  # attach the VM's service account
    ],
    "apis": [
        "compute.googleapis.com",
        "dns.googleapis.com",
    ],
}

# (service label, description, REST probe URL template) - each is a harmless
# read-only GET that verifies the API is enabled AND reachable with these creds.
_PERMISSION_PROBES: list[tuple[str, str, str]] = [
    (
        "Compute Engine",
        "VPC, subnets, firewall, VM, load balancer, certificates",
        "https://compute.googleapis.com/compute/v1/projects/{project}/regions?maxResults=1",
    ),
    (
        "Cloud DNS",
        "Managed zones and A records",
        "https://dns.googleapis.com/dns/v1/projects/{project}/managedZones?maxResults=1",
    ),
]


def check_permissions(gc: GCPCredentials) -> list[GCPPermissionResult]:
    """Probe the APIs provisioning needs. Returns one result per service."""
    _require_gcp()
    try:
        credentials, project = _scoped_credentials(gc)
        session = AuthorizedSession(credentials)
    except Exception as e:
        return [GCPPermissionResult("Credentials", "Authenticate to GCP", False, str(e))]

    results: list[GCPPermissionResult] = []
    for service, description, url_tpl in _PERMISSION_PROBES:
        results.append(_probe(session, service, description, url_tpl.format(project=project)))
    return results


def _probe(session, service: str, description: str, url: str) -> GCPPermissionResult:
    try:
        resp = session.get(url, timeout=15)
    except Exception as e:
        return GCPPermissionResult(service, description, False, str(e))

    if resp.status_code == 200:
        return GCPPermissionResult(service, description, True, None)

    detail = ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except Exception:
        detail = (resp.text or "")[:200]

    if "SERVICE_DISABLED" in (resp.text or "") or "has not been used" in detail:
        return GCPPermissionResult(
            service, description, False, "API not enabled for this project"
        )
    return GCPPermissionResult(service, description, False, detail or f"HTTP {resp.status_code}")
