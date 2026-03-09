"""AWS provider helpers — thin wrappers around boto3."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
)

from iblai_infra.models import AWSCredentials, AuthMethod


# ---------------------------------------------------------------------------
# Data classes for return values
# ---------------------------------------------------------------------------

@dataclass
class CallerIdentity:
    account_id: str
    arn: str
    user_id: str


@dataclass
class HostedZone:
    zone_id: str
    name: str
    record_count: int
    private: bool


@dataclass
class KeyPairInfo:
    name: str
    key_id: str
    key_type: str


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

def get_session(credentials: AWSCredentials) -> boto3.Session:
    """Create a boto3 session from the given credentials."""
    if credentials.method == AuthMethod.PROFILE:
        return boto3.Session(
            profile_name=credentials.profile,
            region_name=credentials.region,
        )
    elif credentials.method == AuthMethod.ACCESS_KEY:
        return boto3.Session(
            aws_access_key_id=credentials.access_key_id,
            aws_secret_access_key=credentials.secret_access_key,
            region_name=credentials.region,
        )
    else:  # ENVIRONMENT
        return boto3.Session(region_name=credentials.region)


# ---------------------------------------------------------------------------
# Credential discovery & validation
# ---------------------------------------------------------------------------

def list_profiles() -> list[str]:
    """List available AWS profiles from ~/.aws/config and ~/.aws/credentials."""
    profiles: set[str] = set()
    for filename in ("config", "credentials"):
        path = Path.home() / ".aws" / filename
        if path.exists():
            parser = configparser.ConfigParser()
            parser.read(path)
            for section in parser.sections():
                # config uses [profile foo], credentials uses [foo]
                name = section.replace("profile ", "")
                profiles.add(name)
    return sorted(profiles)


def has_env_credentials() -> bool:
    """Check if AWS credentials are set via environment variables."""
    import os

    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        and os.environ.get("AWS_SECRET_ACCESS_KEY")
    )


def validate_credentials(credentials: AWSCredentials) -> CallerIdentity:
    """Validate AWS credentials by calling STS. Raises on failure."""
    try:
        session = get_session(credentials)
        sts = session.client("sts")
        resp = sts.get_caller_identity()
        return CallerIdentity(
            account_id=resp["Account"],
            arn=resp["Arn"],
            user_id=resp["UserId"],
        )
    except ProfileNotFound:
        raise ValueError(f"AWS profile '{credentials.profile}' not found")
    except NoCredentialsError:
        raise ValueError("No AWS credentials found")
    except (ClientError, BotoCoreError) as e:
        raise ValueError(f"AWS authentication failed: {e}")


# ---------------------------------------------------------------------------
# Resource discovery
# ---------------------------------------------------------------------------

def list_hosted_zones(session: boto3.Session) -> list[HostedZone]:
    """List Route53 hosted zones."""
    try:
        r53 = session.client("route53")
        zones = []
        paginator = r53.get_paginator("list_hosted_zones")
        for page in paginator.paginate():
            for z in page["HostedZones"]:
                zones.append(
                    HostedZone(
                        zone_id=z["Id"].split("/")[-1],
                        name=z["Name"].rstrip("."),
                        record_count=z["ResourceRecordSetCount"],
                        private=z["Config"].get("PrivateZone", False),
                    )
                )
        return [z for z in zones if not z.private]
    except (ClientError, BotoCoreError):
        return []


def list_key_pairs(session: boto3.Session) -> list[KeyPairInfo]:
    """List existing EC2 key pairs."""
    try:
        ec2 = session.client("ec2")
        resp = ec2.describe_key_pairs()
        return [
            KeyPairInfo(
                name=kp["KeyName"],
                key_id=kp.get("KeyPairId", ""),
                key_type=kp.get("KeyType", "unknown"),
            )
            for kp in resp["KeyPairs"]
        ]
    except (ClientError, BotoCoreError):
        return []


def check_bucket_exists(session: boto3.Session, bucket_name: str) -> bool:
    """Check if an S3 bucket name is already taken (globally)."""
    try:
        s3 = session.client("s3")
        s3.head_bucket(Bucket=bucket_name)
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        # 404 = doesn't exist, 403 = exists but owned by someone else
        if code == "403":
            return True
        return False
    except (BotoCoreError, NoCredentialsError):
        return False


def detect_current_ip() -> str | None:
    """Detect the user's current public IP address."""
    import urllib.request

    try:
        with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# IAM permission checks
# ---------------------------------------------------------------------------

# Minimum IAM policy required for provisioning
REQUIRED_IAM_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "IblaiInfraProvisioning",
            "Effect": "Allow",
            "Action": [
                "ec2:*",
                "elasticloadbalancing:*",
                "s3:*",
                "acm:*",
                "route53:*",
                "iam:UploadServerCertificate",
                "iam:DeleteServerCertificate",
                "iam:GetServerCertificate",
                "iam:ListServerCertificates",
                "sts:GetCallerIdentity",
            ],
            "Resource": "*",
        }
    ],
}

# Dry-run checks: (service_label, test_function_name)
# Each test makes a harmless read-only API call to verify access.
_PERMISSION_CHECKS: list[tuple[str, str, str]] = [
    # (label, service, description)
    ("EC2", "ec2", "Instances, VPC, subnets, security groups, key pairs"),
    ("Elastic Load Balancing", "elbv2", "Application Load Balancer, target groups, listeners"),
    ("S3", "s3", "Buckets for backups, media, static files"),
    ("ACM", "acm", "SSL/TLS certificate provisioning"),
    ("Route 53", "route53", "DNS hosted zones and records"),
    ("IAM", "iam", "Server certificate upload (for cert upload mode)"),
    ("STS", "sts", "Caller identity verification"),
]


@dataclass
class PermissionCheckResult:
    service: str
    description: str
    passed: bool
    error: str | None = None


def check_permissions(session: boto3.Session) -> list[PermissionCheckResult]:
    """Run dry-run permission checks against AWS. Returns results per service."""
    results: list[PermissionCheckResult] = []

    for label, service, description in _PERMISSION_CHECKS:
        try:
            if service == "ec2":
                client = session.client("ec2")
                # DryRun=True tests permission without making changes
                try:
                    client.describe_vpcs(DryRun=True)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "DryRunOperation":
                        pass  # DryRunOperation means permission is granted
                    else:
                        raise
            elif service == "elbv2":
                client = session.client("elbv2")
                client.describe_load_balancers(PageSize=1)
            elif service == "s3":
                client = session.client("s3")
                client.list_buckets(MaxBuckets=1)
            elif service == "acm":
                client = session.client("acm")
                client.list_certificates(MaxItems=1)
            elif service == "route53":
                client = session.client("route53")
                client.list_hosted_zones(MaxItems="1")
            elif service == "iam":
                client = session.client("iam")
                client.list_server_certificates(MaxItems=1)
            elif service == "sts":
                client = session.client("sts")
                client.get_caller_identity()

            results.append(PermissionCheckResult(
                service=label, description=description, passed=True,
            ))
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"].get("Message", code)
            results.append(PermissionCheckResult(
                service=label, description=description, passed=False, error=msg,
            ))
        except (BotoCoreError, NoCredentialsError) as e:
            results.append(PermissionCheckResult(
                service=label, description=description, passed=False, error=str(e),
            ))

    return results
