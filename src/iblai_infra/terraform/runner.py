"""Terraform execution wrapper with Rich Live progress display.

Uses `terraform plan/apply -json` for structured event parsing,
combined with Rich Live + Progress + Table for real-time visual feedback.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.status import Status

from iblai_infra import ui
from iblai_infra.models import (
    CertMethod,
    CloudProvider,
    DeploymentType,
    GCPAuthMethod,
    InfraConfig,
    ProjectState,
    SSHKeyMethod,
)
from iblai_infra.terraform.state import save_state, workspace_dir

# ---------------------------------------------------------------------------
# Resource-type → friendly name mapping
# ---------------------------------------------------------------------------

RESOURCE_LABELS: dict[str, str] = {
    "aws_vpc": "VPC",
    "aws_subnet": "Subnet",
    "aws_internet_gateway": "Internet Gateway",
    "aws_route_table": "Route Table",
    "aws_route_table_association": "Route Table Association",
    "aws_security_group": "Security Group",
    "aws_security_group_rule": "Security Group Rule",
    "aws_lb": "Application Load Balancer",
    "aws_lb_target_group": "Target Group",
    "aws_lb_listener": "ALB Listener",
    "aws_lb_target_group_attachment": "Target Group Attachment",
    "aws_instance": "EC2 Instance",
    "aws_key_pair": "SSH Key Pair",
    "aws_s3_bucket": "S3 Bucket",
    "aws_s3_bucket_policy": "S3 Bucket Policy",
    "aws_s3_bucket_public_access_block": "S3 Access Policy",
    "aws_acm_certificate": "ACM Certificate",
    "aws_acm_certificate_validation": "Certificate Validation",
    "aws_route53_record": "DNS Record",
    "aws_lb_listener_certificate": "ALB Certificate",
    "aws_iam_server_certificate": "IAM Certificate",
    "aws_nat_gateway": "NAT Gateway",
    "aws_eip": "Elastic IP",
    "aws_db_instance": "RDS Database",
    "aws_db_subnet_group": "DB Subnet Group",
    "aws_elasticache_replication_group": "Redis Cluster",
    "aws_elasticache_subnet_group": "Cache Subnet Group",
    "aws_efs_file_system": "EFS File System",
    "aws_efs_mount_target": "EFS Mount Target",
    # GCP (single-server)
    "google_compute_network": "VPC Network",
    "google_compute_subnetwork": "Subnet",
    "google_compute_firewall": "Firewall Rule",
    "google_compute_instance": "Compute Instance",
    "google_compute_instance_group": "Instance Group",
    "google_compute_health_check": "Health Check",
    "google_compute_backend_service": "Backend Service",
    "google_compute_global_address": "Global IP",
    "google_compute_url_map": "URL Map",
    "google_compute_target_http_proxy": "HTTP Proxy",
    "google_compute_target_https_proxy": "HTTPS Proxy",
    "google_compute_global_forwarding_rule": "Forwarding Rule",
    "google_compute_ssl_policy": "SSL Policy",
    "google_compute_managed_ssl_certificate": "Managed Certificate",
    "google_compute_ssl_certificate": "SSL Certificate",
    "google_dns_managed_zone": "DNS Zone",
    "google_dns_record_set": "DNS Record",
}


def _friendly_label(addr: str) -> str:
    """Convert 'aws_vpc.main' → 'VPC (main)', 'aws_subnet.public[0]' → 'Subnet (public)'."""
    parts = addr.split(".")
    if len(parts) >= 2:
        resource_type = parts[0]
        resource_name = re.sub(r"\[.*\]", "", parts[1])  # strip index
        label = RESOURCE_LABELS.get(resource_type, resource_type)
        return f"{label} ({resource_name})"
    return addr


def _is_data_source(addr: str) -> bool:
    """Check if a Terraform address is a data source (not a managed resource)."""
    return addr.startswith("data.")


class TerraformRunner:
    """Manages Terraform workspace and execution lifecycle."""

    def __init__(self, config: InfraConfig):
        self.config = config
        self.ws = workspace_dir(config)
        self.state = ProjectState(
            name=config.project_name,
            config=config,
            workspace_path=str(self.ws),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Copy templates and generate tfvars. Does not run Terraform."""
        self._check_terraform_installed()
        self._copy_templates()
        self._generate_tfvars()
        save_state(self.state)
        ui.success(f"Workspace ready  [muted]{self.ws}[/muted]")

    def reapply(self) -> dict:
        """Re-run Terraform on an existing workspace with the latest ``state.config``.

        Used by post-provision feature toggles (e.g. ``iblai infra waf
        enable``) where the caller has mutated ``state.config`` and wants
        Terraform to converge to the new desired state.

        Re-copies ``.tf`` templates so template fixes propagate, then reads
        the existing ``terraform.tfvars`` to extract the original
        ``bucket_suffix`` (so the S3 bucket names stay stable across re-runs
        — see :meth:`_generate_tfvars`), regenerates the rest of tfvars from
        ``state.config``, and runs ``init`` → ``plan`` → ``apply``.

        Returns the parsed Terraform outputs (same shape as
        :meth:`_get_outputs`). When the plan is a no-op, returns the current
        outputs without running apply.

        Callers are responsible for:
          - confirming the workspace exists and is in the right state,
          - mutating ``self.state.config`` BEFORE calling,
          - merging the returned outputs into ``self.state.outputs`` and
            persisting via ``save_state``.
        """
        self._check_terraform_installed()
        if not self.ws.exists() or not (self.ws / "main.tf").exists():
            raise FileNotFoundError(
                f"Terraform workspace not found at {self.ws}. "
                "Run `iblai infra provision` to create the stack first."
            )

        pinned_suffix = self._read_existing_bucket_suffix()

        self._copy_templates()
        ui.success(f"Templates refreshed  [muted]{self.ws}[/muted]")

        self._generate_tfvars(bucket_suffix=pinned_suffix)

        self.init()
        change_count = self.plan()
        if change_count == 0:
            ui.info("No infrastructure changes required.")
            return self._get_outputs()

        return self.apply()

    def _read_existing_bucket_suffix(self) -> str | None:
        """Parse ``bucket_suffix = "..."`` from the workspace tfvars file.

        Returns the string value if present (possibly empty), or ``None``
        when the line is absent. ``None`` triggers AWS-side resolution in
        :meth:`_generate_tfvars`; empty string pins to no suffix.
        """
        tfvars_path = self.ws / "terraform.tfvars"
        if not tfvars_path.exists():
            return None
        for line in tfvars_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("bucket_suffix"):
                _, _, value = stripped.partition("=")
                return value.strip().strip('"')
        # No bucket_suffix line means the first apply found bucket names were
        # available — pin to "" so we don't unexpectedly add a suffix now.
        return ""

    def init(self) -> None:
        """Run terraform init."""
        with Status("  [info]Initializing Terraform...[/info]", console=ui.console):
            self._run("init", "-input=false")
        ui.success("Terraform initialized")

    def plan(self) -> int:
        """Run terraform plan -json. Returns the number of resources to add."""
        resource_count = 0

        with Status("  [info]Planning infrastructure...[/info]", console=ui.console):
            proc = subprocess.Popen(
                ["terraform", "plan", "-out=tfplan", "-input=false", "-json"],
                cwd=self.ws,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._env(),
            )

            for line in proc.stdout:
                event = self._parse_json_line(line)
                if not event:
                    continue

                # change_summary gives us the total
                if event.get("type") == "change_summary":
                    changes = event.get("changes", {})
                    resource_count = (
                        changes.get("add", 0)
                        + changes.get("change", 0)
                        + changes.get("remove", 0)
                    )

                # Surface diagnostics (warnings/errors)
                if event.get("type") == "diagnostic":
                    severity = event.get("diagnostic", {}).get("severity", "")
                    summary = event.get("diagnostic", {}).get("summary", "")
                    if severity == "error":
                        ui.error(summary)
                    elif severity == "warning":
                        ui.warning(summary)

            proc.wait()

        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            if stderr.strip():
                for errline in stderr.strip().splitlines()[-5:]:
                    ui.error(errline)
            ui.abort("Terraform plan failed.")

        if resource_count > 0:
            ui.success(f"Plan: [highlight]{resource_count}[/highlight] resource(s) to create")
        else:
            ui.info("No changes detected")

        return resource_count

    def apply(self) -> dict:
        """Run terraform apply -json with a live progress display. Returns outputs."""
        ui.newline()

        # Track all resources keyed by Terraform address
        resources: dict[str, dict] = {}
        completed = 0
        errors: list[str] = []

        # We get total from plan output stored in .tfplan
        total = self._count_planned_resources()

        progress = ui.make_overall_progress()
        task_id = progress.add_task("Provisioning infrastructure", total=max(total, 1))

        proc = subprocess.Popen(
            ["terraform", "apply", "-json", "-auto-approve", "tfplan"],
            cwd=self.ws,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self._env(),
        )

        # Compose live display: resource table + progress bar
        with Live(
            self._build_display(resources, progress),
            console=ui.console,
            refresh_per_second=4,
            transient=True,
        ) as live:
            for line in proc.stdout:
                event = self._parse_json_line(line)
                if not event:
                    continue

                msg_type = event.get("type", "")

                if msg_type == "apply_start":
                    addr = event["hook"]["resource"]["addr"]
                    resources[addr] = {
                        "label": _friendly_label(addr),
                        "action": event["hook"].get("action", "create"),
                        "status": "in_progress",
                        "elapsed": 0,
                    }

                elif msg_type == "apply_progress":
                    addr = event["hook"]["resource"]["addr"]
                    if addr in resources:
                        resources[addr]["elapsed"] = event["hook"].get("elapsed_seconds", 0)

                elif msg_type == "apply_complete":
                    addr = event["hook"]["resource"]["addr"]
                    elapsed = event["hook"].get("elapsed_seconds", 0)
                    if addr in resources:
                        resources[addr]["status"] = "complete"
                        resources[addr]["elapsed"] = elapsed
                    else:
                        resources[addr] = {
                            "label": _friendly_label(addr),
                            "status": "complete",
                            "elapsed": elapsed,
                        }
                    completed += 1
                    progress.update(task_id, completed=completed)

                elif msg_type == "apply_errored":
                    addr = event.get("hook", {}).get("resource", {}).get("addr", "unknown")
                    if addr in resources:
                        resources[addr]["status"] = "error"
                    # Error detail comes via separate "diagnostic" events, not here

                elif msg_type == "diagnostic":
                    diag = event.get("diagnostic", {})
                    if diag.get("severity") == "error":
                        summary = diag.get("summary", "")
                        detail = diag.get("detail", "")
                        msg = f"{summary}: {detail}" if summary and detail else (summary or detail or "Unknown error")
                        errors.append(msg)

                # Refresh the live display
                live.update(self._build_display(resources, progress))

            proc.wait()

        # Final output after live display clears
        if proc.returncode != 0:
            self.state.status = "failed"
            save_state(self.state)
            ui.newline()
            # Print the final resource table as static output
            self._print_final_table(resources)
            for e in errors:
                ui.error(e)
            ui.abort("Terraform apply failed. See errors above.")

        # Print the completed table
        self._print_final_table(resources)
        ui.success(
            f"[highlight]{completed}[/highlight] resource(s) created"
        )

        # Get outputs
        outputs = self._get_outputs()
        self.state.status = "created"
        self.state.outputs = outputs
        save_state(self.state)

        self._maybe_gcp_cert_notice(outputs)

        return outputs

    def destroy(self) -> None:
        """Run terraform destroy -json with live progress."""
        ui.newline()

        resources: dict[str, dict] = {}
        completed = 0

        progress = ui.make_overall_progress()
        task_id = progress.add_task("Destroying infrastructure", total=1)

        proc = subprocess.Popen(
            ["terraform", "destroy", "-json", "-auto-approve", "-input=false"],
            cwd=self.ws,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self._env(),
        )

        with Live(
            self._build_display(resources, progress, title="Destroying Resources", destroying=True),
            console=ui.console,
            refresh_per_second=4,
            transient=True,
        ) as live:
            for line in proc.stdout:
                event = self._parse_json_line(line)
                if not event:
                    continue

                msg_type = event.get("type", "")

                if msg_type == "change_summary":
                    total = event.get("changes", {}).get("remove", 0)
                    progress.update(task_id, total=max(total, 1))

                elif msg_type == "apply_start":
                    addr = event["hook"]["resource"]["addr"]
                    if not _is_data_source(addr):
                        resources[addr] = {
                            "label": _friendly_label(addr),
                            "status": "in_progress",
                            "elapsed": 0,
                        }

                elif msg_type == "apply_progress":
                    addr = event["hook"]["resource"]["addr"]
                    if addr in resources:
                        resources[addr]["elapsed"] = event["hook"].get("elapsed_seconds", 0)

                elif msg_type == "apply_complete":
                    addr = event["hook"]["resource"]["addr"]
                    if _is_data_source(addr):
                        continue
                    elapsed = event["hook"].get("elapsed_seconds", 0)
                    if addr in resources:
                        resources[addr]["status"] = "complete"
                        resources[addr]["elapsed"] = elapsed
                    completed += 1
                    progress.update(task_id, completed=completed)

                live.update(
                    self._build_display(resources, progress, title="Destroying Resources", destroying=True)
                )

            proc.wait()

        if proc.returncode != 0:
            self.state.status = "failed"
            save_state(self.state)
            ui.abort("Terraform destroy failed.")

        self.state.status = "destroyed"
        self.state.outputs = None
        save_state(self.state)
        ui.success(f"All infrastructure destroyed ({completed} resources removed)")

    def get_outputs(self) -> dict:
        """Read current terraform outputs."""
        return self._get_outputs()

    def _maybe_gcp_cert_notice(self, outputs: dict) -> None:
        """On a GCP managed-cert apply, HTTPS validates asynchronously (Terraform
        does not block on it). Surface that, plus any nameservers to delegate."""
        c = self.config
        if c.cloud != CloudProvider.GCP:
            return
        if c.certificates.method == CertMethod.MANAGED:
            ui.info(
                "Google-managed certificate is provisioning. HTTPS can take "
                "10-60 minutes to go live after DNS resolves to the load balancer."
            )
        nameservers = outputs.get("dns_name_servers") or []
        if nameservers:
            ui.warning("Delegate your domain at its registrar to these nameservers:")
            for ns in nameservers:
                ui.muted(f"  {ns}")

    # ------------------------------------------------------------------
    # Live display composition
    # ------------------------------------------------------------------

    def _build_display(
        self,
        resources: dict[str, dict],
        progress: ui.Progress,
        title: str = "Provisioning Resources",
        destroying: bool = False,
    ) -> Group:
        """Compose the live display: resource table + progress bar."""
        table = ui.build_resource_table(resources, destroying=destroying)
        return Group(
            ui.section_group(title, table),
            progress,
        )

    def _print_final_table(self, resources: dict[str, dict], destroying: bool = False) -> None:
        """Print the resource table one final time (static, not live)."""
        if not resources:
            return
        table = ui.build_resource_table(resources, destroying=destroying)
        ui.section("Resources", table)

    # ------------------------------------------------------------------
    # Plan resource counting
    # ------------------------------------------------------------------

    def _count_planned_resources(self) -> int:
        """Run terraform show -json tfplan to count planned resources."""
        try:
            result = subprocess.run(
                ["terraform", "show", "-json", "tfplan"],
                cwd=self.ws,
                capture_output=True,
                text=True,
                env=self._env(),
            )
            if result.returncode == 0 and result.stdout.strip():
                plan = json.loads(result.stdout)
                changes = plan.get("resource_changes", [])
                return sum(
                    1 for c in changes
                    if any(a in c.get("change", {}).get("actions", []) for a in ("create", "update", "delete"))
                )
        except Exception:
            pass
        return 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_terraform_installed(self) -> None:
        if shutil.which("terraform") is None:
            ui.error("Terraform is not installed or not in PATH")
            ui.info("Install: https://developer.hashicorp.com/terraform/install")
            ui.abort()

    def _copy_templates(self) -> None:
        """Copy Terraform template files to workspace."""
        self.ws.mkdir(parents=True, exist_ok=True)
        # topology: "single-server" / "multi-server" / "call-server"
        # cloud:    "aws" / "gcp"
        topology = self.config.deployment_type.value
        template_dir = Path(__file__).parent / "templates" / self.config.cloud.value / topology
        if not template_dir.exists():
            ui.abort(f"Template directory not found: {template_dir}")
        for f in template_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, self.ws / f.name)

    def _resolve_bucket_suffix(self, config: InfraConfig) -> str:
        """Check if default S3 bucket names are taken; return date suffix if so."""
        from datetime import datetime, timezone

        from iblai_infra.providers.aws import check_bucket_exists, get_session

        domain_slug = config.dns.base_domain.replace(".", "-")
        prefix = f"{config.project_name}-{config.environment.value}-{domain_slug}"
        test_bucket = f"{prefix}-backups"

        try:
            session = get_session(config.credentials)
            if check_bucket_exists(session, test_bucket):
                suffix = datetime.now(timezone.utc).strftime("%d%m%Y")
                ui.warning(
                    f"S3 bucket [highlight]{test_bucket}[/highlight] already exists, "
                    f"appending [highlight]{suffix}[/highlight] to bucket names"
                )
                return suffix
        except Exception:
            pass
        return ""

    def _generate_tfvars(self, bucket_suffix: str | None = None) -> None:
        """Generate terraform.tfvars from InfraConfig.

        When ``bucket_suffix`` is None, resolve it from AWS (today's behaviour
        — used by the first ``setup()`` call). When provided, use the pinned
        value. Used by :meth:`reapply` so re-runs against the same project
        keep the same S3 bucket names even after the date-stamp window has
        rolled over.
        """
        c = self.config
        lines: list[str] = []

        def tf(key: str, value: str | int | bool | list[str]) -> None:
            if isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, int):
                lines.append(f"{key} = {value}")
            elif isinstance(value, list):
                items = ", ".join(json.dumps(v) for v in value)
                lines.append(f"{key} = [{items}]")
            else:
                lines.append(f'{key} = "{value}"')

        # GCP single-server has its own variable set (project/zone/machine_type/
        # image/subnet_cidr/dns_zone). No S3 buckets, no AWS creds, no WAF.
        if c.cloud == CloudProvider.GCP:
            self._generate_gcp_tfvars(c, tf, lines)
            return

        tf("project_name", c.project_name)
        tf("environment", c.environment.value)
        tf("region", c.credentials.region)
        tf("instance_type", c.compute.instance_type)
        tf("root_volume_size", c.compute.volume_size)
        tf("root_volume_type", c.compute.volume_type)
        if c.compute.ami_id:
            tf("ami_id", c.compute.ami_id)
            tf("skip_user_data", True)
        tf("vpc_cidr", c.network.vpc_cidr)
        tf("vpn_ip", c.network.vpn_ip)
        tf("base_domain", c.dns.base_domain)

        # SSH (shared across all deployment types)
        if c.ssh.method == SSHKeyMethod.AWS_KEYPAIR:
            tf("existing_key_pair_name", c.ssh.key_name)
            tf("create_key_pair", False)
        else:
            tf("ssh_public_key", c.ssh.public_key or "")
            tf("key_pair_name", c.ssh.key_name)
            tf("create_key_pair", True)

        # Call-server has its own variable set — no ALB, no ACM cert (LiveKit
        # handles TLS in-process), no S3 buckets. hosted_zone_id alone drives
        # optional R53 A-record creation.
        if c.deployment_type == DeploymentType.CALL:
            cs = c.call_server
            tf("hosted_zone_id", c.certificates.hosted_zone_id or "")
            tf("enable_sip", cs.enable_sip if cs else False)
            (self.ws / "terraform.tfvars").write_text("\n".join(lines) + "\n")
            return

        # S3 bucket uniqueness — pinned by caller (reapply path) or resolved
        # from AWS on first apply. The pinned path is what prevents bucket
        # rename / destroy on subsequent applies once the date-stamp window
        # has rolled over.
        if bucket_suffix is None:
            bucket_suffix = self._resolve_bucket_suffix(c)
        if bucket_suffix:
            tf("bucket_suffix", bucket_suffix)

        # Certificates (ALB-attached — single-server and multi-server only)
        if c.certificates.method == CertMethod.ACM:
            tf("hosted_zone_id", c.certificates.hosted_zone_id or "")
            tf("certificate_method", "acm")
        elif c.certificates.method == CertMethod.UPLOAD:
            tf("certificate_method", "upload")
            if c.certificates.cert_body:
                (self.ws / "cert.pem").write_text(c.certificates.cert_body)
                tf("certificate_body_file", "cert.pem")
            if c.certificates.cert_private_key:
                (self.ws / "cert-key.pem").write_text(c.certificates.cert_private_key)
                tf("certificate_key_file", "cert-key.pem")
            if c.certificates.cert_chain:
                (self.ws / "cert-chain.pem").write_text(c.certificates.cert_chain)
                tf("certificate_chain_file", "cert-chain.pem")
        else:
            tf("certificate_method", "none")

        # Multi-server specific variables
        if c.deployment_type == DeploymentType.MULTI and c.multi_server:
            ms = c.multi_server
            tf("app_server_count", ms.app_server_count)
            tf("app_server_instance_type", ms.app_server_instance_type)
            tf("app_server_volume_size", ms.app_server_volume_size)
            tf("services_instance_type", ms.services_instance_type)
            tf("services_volume_size", ms.services_volume_size)
            tf("enable_mysql", ms.enable_mysql)
            tf("enable_postgres", ms.enable_postgres)
            tf("enable_redis", ms.enable_redis)
            if ms.enable_mysql:
                tf("rds_mysql_instance_class", ms.mysql_instance_class)
                tf("rds_mysql_storage_size", ms.mysql_storage_size)
                tf("mysql_password", ms.mysql_password or "")
            if ms.enable_postgres:
                tf("rds_postgres_instance_class", ms.postgres_instance_class)
                tf("rds_postgres_storage_size", ms.postgres_storage_size)
                tf("postgres_password", ms.postgres_password or "")
            if ms.enable_redis:
                tf("redis_instance_type", ms.redis_instance_type)
                tf("redis_auth_token", ms.redis_auth_token or "")

        # WAF (single-server only — multi-server ALB and call-server have no
        # WAF wiring in their templates today). When disabled, emit
        # `enable_waf = false` so the variable always has a value and the
        # waf.tf locals don't reference an undefined input.
        if c.deployment_type == DeploymentType.SINGLE:
            if c.waf and c.waf.enabled:
                tf("enable_waf", True)
                tf("waf_allowed_ips", c.waf.allowed_ips)
            else:
                tf("enable_waf", False)

        (self.ws / "terraform.tfvars").write_text("\n".join(lines) + "\n")

    def _generate_gcp_tfvars(self, c: InfraConfig, tf, lines: list[str]) -> None:
        """Emit terraform.tfvars for a GCP single-server stack.

        Reuses the provider-neutral config sections: ``compute.instance_type`` is
        the GCP machine type, ``compute.volume_type`` the disk type,
        ``compute.ami_id`` a custom image, ``network.vpc_cidr`` the subnet range.
        """
        gc = c.gcp_credentials
        tf("project_id", gc.project_id if gc else "")
        tf("region", gc.region if gc else "us-central1")
        tf("zone", gc.zone if gc else "us-central1-a")
        tf("project_name", c.project_name)
        tf("environment", c.environment.value)
        tf("base_domain", c.dns.base_domain)
        tf("subnet_cidr", c.network.vpc_cidr)
        tf("vpn_ip", c.network.vpn_ip)
        tf("machine_type", c.compute.instance_type)
        tf("volume_size", c.compute.volume_size)
        tf("disk_type", c.compute.volume_type)
        if c.compute.ami_id:
            tf("image", c.compute.ami_id)
            tf("skip_startup_script", True)
        if c.ssh.public_key:
            tf("ssh_public_key", c.ssh.public_key)

        if c.certificates.method == CertMethod.MANAGED:
            tf("certificate_method", "managed")
            tf("dns_zone_name", c.dns.dns_zone_name or "")
            tf("create_dns_zone", c.dns.create_dns_zone)
        elif c.certificates.method == CertMethod.UPLOAD:
            tf("certificate_method", "upload")
            if c.certificates.cert_body:
                (self.ws / "cert.pem").write_text(c.certificates.cert_body)
                tf("certificate_body_file", "cert.pem")
            if c.certificates.cert_private_key:
                (self.ws / "cert-key.pem").write_text(c.certificates.cert_private_key)
                tf("certificate_key_file", "cert-key.pem")
        else:
            tf("certificate_method", "none")

        (self.ws / "terraform.tfvars").write_text("\n".join(lines) + "\n")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        """Run a terraform command in the workspace."""
        result = subprocess.run(
            ["terraform", *args],
            cwd=self.ws,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            ui.error(f"terraform {args[0]} failed:")
            for errline in stderr.splitlines()[-10:]:
                ui.muted(f"  {errline}")
            ui.abort()
        return result

    def _get_outputs(self) -> dict:
        """Parse terraform output -json."""
        try:
            result = subprocess.run(
                ["terraform", "output", "-json"],
                cwd=self.ws,
                capture_output=True,
                text=True,
                env=self._env(),
            )
            if result.returncode == 0 and result.stdout.strip():
                raw = json.loads(result.stdout)
                return {k: v.get("value") for k, v in raw.items()}
        except Exception:
            pass
        return {}

    def _env(self) -> dict[str, str]:
        """Build environment for Terraform subprocess."""
        env = os.environ.copy()
        env["TF_INPUT"] = "0"

        if self.config.cloud == CloudProvider.GCP:
            gc = self.config.gcp_credentials
            if gc:
                env["GOOGLE_PROJECT"] = gc.project_id
                env["CLOUDSDK_CORE_PROJECT"] = gc.project_id
                env["CLOUDSDK_COMPUTE_REGION"] = gc.region
                env["CLOUDSDK_COMPUTE_ZONE"] = gc.zone
                # ADC (gcloud auth application-default login) needs no env var;
                # a service-account key is passed by file path.
                if gc.method == GCPAuthMethod.SERVICE_ACCOUNT_KEY and gc.credentials_file:
                    env["GOOGLE_APPLICATION_CREDENTIALS"] = gc.credentials_file
            return env

        c = self.config.credentials
        if c:
            if c.profile:
                env["AWS_PROFILE"] = c.profile
            if c.access_key_id:
                env["AWS_ACCESS_KEY_ID"] = c.access_key_id
            if c.secret_access_key:
                env["AWS_SECRET_ACCESS_KEY"] = c.secret_access_key
            env["AWS_DEFAULT_REGION"] = c.region
        return env

    @staticmethod
    def _parse_json_line(line: str) -> dict | None:
        """Safely parse a JSON line from terraform -json output."""
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
