"""Keep the DM's two S3 buckets in the configuration the platform needs.

The DM keeps static files and uploaded media in two buckets with opposite
access rules:

- **dm-static** is public-read: browsers load the DM's CSS, JS and fonts
  straight from it. Fonts and script fetches are cross-origin, so it also needs
  a CORS rule for GET/HEAD. Its content is public, so any origin may read it.
- **dm-media** is private: the DM hands out presigned URLs. Chat attachments
  are a presigned PUT sent straight from the browser, so the bucket needs a
  CORS rule for GET/HEAD/PUT/POST from the platform's origins. Without one, S3
  answers the browser's preflight with 403 "CORS is not enabled for this
  bucket" and every upload fails, while the DM's own logs show only 200s.

`check_dm_buckets` reports. Its CORS verdict is the same preflight a browser
sends, so it needs no S3 permissions at all. `ensure_dm_buckets` repairs, and
only ever adds: existing CORS rules and policy statements are kept, and our
CORS rule goes first because S3 applies the first rule that matches.
"""

from __future__ import annotations

import fnmatch
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from botocore.exceptions import ClientError

# Permissions, for the hint shown when a call is refused.
CHECK_PERMISSIONS = (
    "s3:GetBucketLocation", "s3:GetBucketCORS", "s3:GetBucketPolicyStatus",
    "s3:GetBucketPolicy", "s3:GetBucketPublicAccessBlock", "s3:ListBucket",
)
FIX_PERMISSIONS = ("s3:PutBucketCORS", "s3:PutBucketPolicy", "s3:PutBucketPublicAccessBlock")

CORS_MAX_AGE = 3000


class BucketRole(str, Enum):
    STATIC = "static"
    MEDIA = "media"


@dataclass
class Finding:
    bucket: str
    role: BucketRole
    check: str
    # True: as required. False: wrong. None: could not tell (no permission and
    # nothing to probe), which never fails a check on its own.
    ok: bool | None
    detail: str
    fixable: bool = True


@dataclass
class BucketReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def failed(self) -> list[Finding]:
        return [f for f in self.findings if f.ok is False]

    @property
    def unknown(self) -> list[Finding]:
        return [f for f in self.findings if f.ok is None]

    @property
    def ok(self) -> bool:
        return not self.failed


# ---------------------------------------------------------------------------
# The required configuration
# ---------------------------------------------------------------------------

def platform_origins(base_domain: str, extra_origins: tuple[str, ...] = ()) -> list[str]:
    """The origins the platform's apps are served from, plus any tenant custom domains."""
    origins = [f"https://{base_domain}", f"https://*.{base_domain}"]
    return origins + [o for o in extra_origins if o not in origins]


def required_cors_rule(role: BucketRole, base_domain: str, extra_origins: tuple[str, ...] = ()) -> dict:
    if role is BucketRole.STATIC:
        return {
            "AllowedOrigins": ["*"],
            "AllowedMethods": ["GET", "HEAD"],
            "AllowedHeaders": ["*"],
            "MaxAgeSeconds": CORS_MAX_AGE,
        }
    return {
        "AllowedOrigins": platform_origins(base_domain, extra_origins),
        "AllowedMethods": ["GET", "HEAD", "PUT", "POST"],
        "AllowedHeaders": ["*"],
        # Multipart uploads read each part's ETag from the response.
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": CORS_MAX_AGE,
    }


def _probe(role: BucketRole, base_domain: str) -> tuple[str, str, str]:
    """(origin, method, request header) of the request the browser actually makes."""
    origin = f"https://os.{base_domain}"
    if role is BucketRole.STATIC:
        return origin, "GET", ""
    return origin, "PUT", "content-type"


def _origin_allowed(pattern: str, origin: str) -> bool:
    return pattern == "*" or fnmatch.fnmatchcase(origin, pattern)


def _first_match(rules: list[dict], origin: str, method: str, header: str) -> dict | None:
    """The rule S3 applies to this request: the first whose origin, method and headers match."""
    for rule in rules:
        if method not in rule.get("AllowedMethods", []):
            continue
        if not any(_origin_allowed(p, origin) for p in rule.get("AllowedOrigins", [])):
            continue
        headers = [h.lower() for h in rule.get("AllowedHeaders", [])]
        if header and not any(h == "*" or h == header for h in headers):
            continue
        return rule
    return None


def cors_satisfied(rules: list[dict], role: BucketRole, base_domain: str) -> bool:
    origin, method, header = _probe(role, base_domain)
    rule = _first_match(rules, origin, method, header)
    if rule is None:
        return False
    if role is BucketRole.MEDIA:
        return "etag" in [h.lower() for h in rule.get("ExposeHeaders", [])]
    return True


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------

def _code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


def _denied(exc: ClientError) -> bool:
    return _code(exc) in ("AccessDenied", "AllAccessDisabled", "403")


def bucket_url(bucket: str, region: str, key: str = "") -> str:
    # Virtual-hosted, as presigned URLs are; path-style when a dot in the name
    # would break the wildcard TLS certificate.
    if "." in bucket:
        return f"https://s3.{region}.amazonaws.com/{bucket}/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def http_preflight(url: str, origin: str, method: str, header: str, timeout: int = 15) -> tuple[int, str]:
    """Send the browser's CORS preflight. Returns (status, allowed origin or S3's message).

    Status 0 means no answer at all (network), which says nothing about the bucket.
    """
    req = urllib.request.Request(url, method="OPTIONS")
    req.add_header("Origin", origin)
    req.add_header("Access-Control-Request-Method", method)
    if header:
        req.add_header("Access-Control-Request-Headers", header)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Access-Control-Allow-Origin", "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        start, end = body.find("<Message>"), body.find("</Message>")
        return exc.code, body[start + 9:end] if 0 <= start < end else body[:200]
    except (urllib.error.URLError, OSError) as exc:
        return 0, f"no response ({getattr(exc, 'reason', exc)})"


def http_anonymous_head(url: str, timeout: int = 15) -> int:
    """Status of an unsigned HEAD; 0 when there was no answer."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return 0


def _bucket_region(s3, bucket: str, default: str) -> str:
    try:
        return s3.get_bucket_location(Bucket=bucket).get("LocationConstraint") or "us-east-1"
    except ClientError:
        return default


def _cors_rules(s3, bucket: str) -> list[dict] | None:
    """The bucket's CORS rules; [] when it has none, None when we may not read them."""
    try:
        return s3.get_bucket_cors(Bucket=bucket).get("CORSRules", [])
    except ClientError as exc:
        if _code(exc) == "NoSuchCORSConfiguration":
            return []
        if _denied(exc):
            return None
        raise


def _policy_is_public(s3, bucket: str) -> bool | None:
    try:
        return bool(s3.get_bucket_policy_status(Bucket=bucket)["PolicyStatus"]["IsPublic"])
    except ClientError as exc:
        if _code(exc) == "NoSuchBucketPolicy":
            return False
        if _denied(exc):
            return None
        raise


def _sample_key(s3, bucket: str) -> str | None:
    try:
        contents = s3.list_objects_v2(Bucket=bucket, MaxKeys=1).get("Contents", [])
    except ClientError:
        return None
    return contents[0]["Key"] if contents else None


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def _check_bucket(s3, bucket, role, base_domain, region, preflight, anonymous_head) -> list[Finding]:
    findings: list[Finding] = []
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if _code(exc) in ("404", "NoSuchBucket", "NotFound"):
            return [Finding(bucket, role, "exists", False, "bucket does not exist", fixable=False)]
        # 403 means it exists but this key may not HEAD it; carry on with what we can see.
    region = _bucket_region(s3, bucket, region)

    origin, method, header = _probe(role, base_domain)
    status, info = preflight(bucket_url(bucket, region, "cors-probe"), origin, method, header)
    rules = _cors_rules(s3, bucket)
    need = f"{method} from {origin}"
    if status == 200:
        findings.append(Finding(bucket, role, "cors", True, f"browser preflight ({need}) allowed"))
    elif status == 0:
        # No answer from S3: judge the rules themselves, if we may read them.
        if rules is None:
            findings.append(Finding(bucket, role, "cors", None, f"preflight got {info}; rules not readable"))
        elif cors_satisfied(rules, role, base_domain):
            findings.append(Finding(bucket, role, "cors", True, f"rules allow {need} (preflight got {info})"))
        else:
            findings.append(Finding(bucket, role, "cors", False, f"no rule allows {need} (preflight got {info})"))
    else:
        detail = f"browser preflight ({need}) refused: {status} {info}".strip()
        if rules == []:
            detail += "; the bucket has no CORS rules"
        findings.append(Finding(bucket, role, "cors", False, detail))

    public = _policy_is_public(s3, bucket)
    if public is None:
        key = _sample_key(s3, bucket)
        if key is not None:
            code = anonymous_head(bucket_url(bucket, region, key))
            public = None if code == 0 else code == 200
    if role is BucketRole.STATIC:
        if public is None:
            findings.append(Finding(bucket, role, "public-read", None, "could not tell (no permission, nothing to probe)"))
        elif public:
            findings.append(Finding(bucket, role, "public-read", True, "objects are publicly readable"))
        else:
            findings.append(Finding(bucket, role, "public-read", False, "objects are not publicly readable; the DM serves static files from this bucket"))
    else:
        if public is None:
            findings.append(Finding(bucket, role, "private", None, "could not tell (no permission, nothing to probe)"))
        elif public:
            findings.append(Finding(bucket, role, "private", False, "the bucket is public; uploaded media must only be reachable through presigned URLs"))
        else:
            findings.append(Finding(bucket, role, "private", True, "not publicly readable"))
    return findings


def check_dm_buckets(
    session,
    *,
    static_bucket: str,
    media_bucket: str,
    base_domain: str,
    region: str,
    preflight: Callable = http_preflight,
    anonymous_head: Callable = http_anonymous_head,
) -> BucketReport:
    """Read-only. Report how both DM buckets differ from what the platform needs."""
    s3 = session.client("s3", region_name=region)
    report = BucketReport()
    for bucket, role in ((static_bucket, BucketRole.STATIC), (media_bucket, BucketRole.MEDIA)):
        report.findings.extend(_check_bucket(s3, bucket, role, base_domain, region, preflight, anonymous_head))
    return report


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

class BucketFixError(RuntimeError):
    """A repair the caller's credentials may not make."""


def _ensure_cors(s3, bucket, role, base_domain, extra_origins) -> str | None:
    rules = _cors_rules(s3, bucket)
    if rules is None:
        raise BucketFixError(f"{bucket}: not allowed to read its CORS rules (needs s3:GetBucketCORS)")
    if cors_satisfied(rules, role, base_domain):
        return None
    # First, so an existing rule for the same origin can't shadow it.
    new_rules = [required_cors_rule(role, base_domain, extra_origins)] + rules
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": new_rules})
    kept = f", kept {len(rules)} existing" if rules else ""
    return f"{bucket}: added the {role.value} CORS rule{kept}"


def _public_read_statement(bucket: str) -> dict:
    return {
        "Sid": "PublicReadGetObject",
        "Effect": "Allow",
        "Principal": "*",
        "Action": "s3:GetObject",
        "Resource": f"arn:aws:s3:::{bucket}/*",
    }


def _grants_public_read(statement: dict, bucket: str) -> bool:
    actions = statement.get("Action", [])
    actions = [actions] if isinstance(actions, str) else actions
    resources = statement.get("Resource", [])
    resources = [resources] if isinstance(resources, str) else resources
    principal = statement.get("Principal")
    return (
        statement.get("Effect") == "Allow"
        and principal in ("*", {"AWS": "*"})
        and any(a in ("s3:GetObject", "s3:*", "*") for a in actions)
        and any(r in (f"arn:aws:s3:::{bucket}/*", "*") for r in resources)
    )


def _ensure_public_read(s3, bucket) -> list[str]:
    changes = []
    try:
        pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
    except ClientError as exc:
        if _code(exc) != "NoSuchPublicAccessBlockConfiguration":
            raise
        pab = None
    if pab and (pab.get("BlockPublicPolicy") or pab.get("RestrictPublicBuckets")):
        # Only the two flags that cancel a public policy; the ACL flags are the operator's.
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={**pab, "BlockPublicPolicy": False, "RestrictPublicBuckets": False},
        )
        changes.append(f"{bucket}: allowed a public bucket policy (BlockPublicPolicy, RestrictPublicBuckets off)")
    try:
        policy = json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])
    except ClientError as exc:
        if _code(exc) != "NoSuchBucketPolicy":
            raise
        policy = {"Version": "2012-10-17", "Statement": []}
    statements = policy.get("Statement", [])
    statements = [statements] if isinstance(statements, dict) else statements
    if not any(_grants_public_read(s, bucket) for s in statements):
        policy["Statement"] = statements + [_public_read_statement(bucket)]
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        changes.append(f"{bucket}: added a public-read statement to the bucket policy")
    return changes


def _ensure_private(s3, bucket) -> list[str]:
    # Blocking public access neutralises a public policy or ACL without
    # deleting the operator's statements.
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
        },
    )
    return [f"{bucket}: blocked all public access"]


def ensure_dm_buckets(
    session,
    report: BucketReport,
    *,
    base_domain: str,
    region: str,
    extra_origins: tuple[str, ...] = (),
) -> list[str]:
    """Repair what `report` found wrong. Returns one line per change made.

    Raises BucketFixError when a repair needs a permission the credentials lack.
    """
    s3 = session.client("s3", region_name=region)
    changes: list[str] = []
    for f in report.failed:
        if not f.fixable:
            continue
        try:
            if f.check == "cors":
                change = _ensure_cors(s3, f.bucket, f.role, base_domain, extra_origins)
                changes += [change] if change else []
            elif f.check == "public-read":
                changes += _ensure_public_read(s3, f.bucket)
            elif f.check == "private":
                changes += _ensure_private(s3, f.bucket)
        except ClientError as exc:
            if _denied(exc):
                raise BucketFixError(
                    f"{f.bucket}: {exc.operation_name} was refused. Repairing needs "
                    + ", ".join(FIX_PERMISSIONS)
                ) from exc
            raise
    return changes
