"""``iblai infra s3`` — check and repair the DM's S3 buckets.

The platform needs its two DM buckets set up a particular way (see
``iblai_infra.s3_buckets``). A media bucket without a CORS rule breaks every
chat attachment while the DM's logs show only 200s, so the configuration is
checked here rather than trusted:

    iblai infra s3 check -f .env          # buckets, domain and S3 key from a setup .env
    iblai infra s3 check <name>           # buckets from a provisioned project's outputs
    iblai infra s3 fix -f .env --yes      # repair, then check again

``setup-env`` runs the same check before it touches the server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import boto3
import questionary
import typer
from rich.table import Table

from iblai_infra import ui
from iblai_infra.env_utils import load_env_file
from iblai_infra.models import S3_BUCKET_NAME_RE
from iblai_infra.s3_buckets import (
    CHECK_PERMISSIONS,
    FIX_PERMISSIONS,
    BucketFixError,
    BucketReport,
    check_dm_buckets,
    ensure_dm_buckets,
)
from iblai_infra.terraform.state import load_state

s3_app = typer.Typer(
    name="s3",
    help="Check and repair the DM's S3 buckets (CORS, public and private access).",
    no_args_is_help=True,
)


@dataclass
class _Target:
    static_bucket: str
    media_bucket: str
    base_domain: str
    region: str
    session: boto3.Session
    credentials: str


def _resolve_target(name, env_file, static, media, domain, region, profile) -> _Target:
    env = {}
    if env_file:
        if not env_file.exists():
            ui.error(f"No .env file found at: {env_file}")
            raise typer.Exit(1)
        env = load_env_file(env_file)
    state = None
    if name:
        state = load_state(name)
        if state is None:
            ui.error(f"Project '{name}' not found.")
            raise typer.Exit(1)
    outputs = (state.outputs if state else {}) or {}

    static = static or env.get("S3_STATIC_BUCKET", "").strip() or outputs.get("s3_bucket_static", "")
    media = media or env.get("S3_MEDIA_BUCKET", "").strip() or outputs.get("s3_bucket_media", "")
    domain = (domain or env.get("BASE_DOMAIN", "").strip()
              or (state.config.dns.base_domain if state else "")).lower()
    region = (region or env.get("S3_REGION", "").strip() or env.get("AWS_DEFAULT_REGION", "").strip()
              or (state.config.credentials.region if state else "") or "us-east-1")

    missing = [label for label, value in (("static bucket", static), ("media bucket", media), ("base domain", domain)) if not value]
    if missing:
        ui.error(f"Missing: {', '.join(missing)}.")
        ui.muted("Pass a project name, a setup .env (-f), or --static / --media / --domain.")
        raise typer.Exit(1)
    for bucket in (static, media):
        if not S3_BUCKET_NAME_RE.fullmatch(bucket):
            ui.error(f"{bucket!r} is not a valid S3 bucket name.")
            raise typer.Exit(1)

    if profile:
        session, how = boto3.Session(profile_name=profile, region_name=region), f"profile {profile}"
    elif env.get("S3_ACCESS_KEY_ID") and env.get("S3_SECRET_ACCESS_KEY"):
        session = boto3.Session(
            aws_access_key_id=env["S3_ACCESS_KEY_ID"].strip(),
            aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"].strip(),
            region_name=region,
        )
        how = "the S3 key from the .env"
    else:
        from iblai_infra.cli import _resolve_credentials
        from iblai_infra.providers.aws import get_session

        creds, _ = _resolve_credentials(None, region)
        session, how = get_session(creds), "the saved AWS session"
    return _Target(static, media, domain, region, session, how)


def render_report(report: BucketReport) -> None:
    table = Table(box=None, header_style="brand", pad_edge=False)
    for col in ("Bucket", "Role", "Check", "", "Detail"):
        table.add_column(col)
    for f in report.findings:
        mark = {True: "[success]OK[/success]", False: "[error]FAIL[/error]", None: "[warning]?[/warning]"}[f.ok]
        table.add_row(f.bucket, f.role.value, f.check, mark, f.detail)
    ui.console.print(table)
    if report.unknown:
        ui.muted(f"  ? = could not tell. Reading everything needs: {', '.join(CHECK_PERMISSIONS)}")


def _check(t: _Target) -> BucketReport:
    return check_dm_buckets(
        t.session, static_bucket=t.static_bucket, media_bucket=t.media_bucket,
        base_domain=t.base_domain, region=t.region,
    )


def _repair(t: _Target, report: BucketReport, extra_origins: tuple[str, ...]) -> BucketReport:
    """Apply the fixes, then check again. S3 can take a few seconds to serve new CORS rules."""
    changes = ensure_dm_buckets(
        t.session, report, base_domain=t.base_domain, region=t.region, extra_origins=extra_origins,
    )
    for change in changes:
        ui.success(change)
    after = _check(t)
    for _ in range(5):
        if after.ok:
            break
        time.sleep(3)
        after = _check(t)
    return after


@s3_app.command("check")
def check(
    name: str = typer.Argument(None, help="Provisioned project (buckets from its Terraform outputs)."),
    env_file: Path = typer.Option(None, "--env-file", "-f", help="Setup .env with S3_STATIC_BUCKET, S3_MEDIA_BUCKET, BASE_DOMAIN and the S3 key."),
    static: str = typer.Option("", "--static", help="Static bucket name."),
    media: str = typer.Option("", "--media", help="Media bucket name."),
    domain: str = typer.Option("", "--domain", help="Platform base domain."),
    region: str = typer.Option("", "--region", help="Bucket region (detected when the key may read it)."),
    profile: str = typer.Option(None, "--profile", help="AWS profile to use instead of the .env's S3 key."),
) -> None:
    """Report how the DM buckets differ from what the platform needs. Read-only."""
    t = _resolve_target(name, env_file, static, media, domain, region, profile)
    ui.info(f"Checking {t.static_bucket} (static) and {t.media_bucket} (media) for {t.base_domain}, using {t.credentials}")
    report = _check(t)
    render_report(report)
    if report.ok:
        ui.success("Both buckets are configured the way the platform needs.")
        return
    ui.error(f"{len(report.failed)} problem(s). Repair with: [brand]iblai infra s3 fix[/brand] and the same arguments.")
    raise typer.Exit(1)


@s3_app.command("fix")
def fix(
    name: str = typer.Argument(None, help="Provisioned project (buckets from its Terraform outputs)."),
    env_file: Path = typer.Option(None, "--env-file", "-f", help="Setup .env with S3_STATIC_BUCKET, S3_MEDIA_BUCKET, BASE_DOMAIN and the S3 key."),
    static: str = typer.Option("", "--static", help="Static bucket name."),
    media: str = typer.Option("", "--media", help="Media bucket name."),
    domain: str = typer.Option("", "--domain", help="Platform base domain."),
    region: str = typer.Option("", "--region", help="Bucket region (detected when the key may read it)."),
    profile: str = typer.Option(None, "--profile", help="AWS profile to use instead of the .env's S3 key."),
    extra_origin: list[str] = typer.Option(
        [], "--extra-origin",
        help="Another origin allowed to upload to the media bucket, e.g. a tenant's custom domain (repeatable).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before changing the buckets."),
) -> None:
    """Repair the DM buckets: add the missing CORS rules, open static, close media.

    Only adds: existing CORS rules and policy statements are kept.
    """
    t = _resolve_target(name, env_file, static, media, domain, region, profile)
    report = _check(t)
    render_report(report)
    if report.ok:
        ui.success("Nothing to fix.")
        return
    unfixable = [f for f in report.failed if not f.fixable]
    if unfixable:
        for f in unfixable:
            ui.error(f"{f.bucket}: {f.detail}")
        raise typer.Exit(1)
    if not yes and not questionary.confirm(
        f"Change the bucket configuration of {t.static_bucket} and {t.media_bucket}?", default=False,
    ).ask():
        ui.abort()
    try:
        after = _repair(t, report, tuple(extra_origin))
    except BucketFixError as exc:
        ui.error(str(exc))
        ui.muted("Use credentials that may change the bucket configuration, e.g. --profile <admin profile>.")
        raise typer.Exit(1)
    render_report(after)
    if not after.ok:
        ui.error("Still not right after the repair; see the table above.")
        raise typer.Exit(1)
    ui.success("Both buckets are now configured the way the platform needs.")


def verify_for_setup(setup_config, env_file: Path, *, fix: bool) -> None:
    """The setup-env gate: stop before the server is touched when the buckets are wrong."""
    session = boto3.Session(
        aws_access_key_id=setup_config.s3_access_key_id,
        aws_secret_access_key=setup_config.s3_secret_access_key,
        region_name=setup_config.s3_region,
    )
    t = _Target(
        setup_config.s3_static_bucket, setup_config.s3_media_bucket,
        setup_config.base_domain, setup_config.s3_region, session, "the S3 key",
    )
    ui.info(f"Checking the DM buckets {t.static_bucket} and {t.media_bucket}")
    report = _check(t)
    if report.ok:
        ui.success("DM buckets are configured correctly.")
        return
    render_report(report)
    if fix and all(f.fixable for f in report.failed):
        try:
            report = _repair(t, report, ())
        except BucketFixError as exc:
            ui.error(str(exc))
        if report.ok:
            ui.success("DM buckets repaired.")
            return
        render_report(report)
    ui.error("The DM's S3 buckets are not configured the way the platform needs.")
    ui.muted(f"  Repair them: [brand]iblai infra s3 fix -f {env_file}[/brand] (needs {', '.join(FIX_PERMISSIONS)})")
    ui.muted("  or re-run with --fix-buckets, or --skip-bucket-check to go ahead anyway.")
    raise typer.Exit(1)
