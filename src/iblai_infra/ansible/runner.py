"""Ansible execution wrapper with Rich Live progress display.

Runs ansible-playbook with all roles (infrastructure + platform) and tracks
progress via line-based output parsing of the default callback.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.table import Table

from iblai_infra import ui
from iblai_infra.models import DeploymentType, ProjectState, SetupConfig, parse_repo_path
from iblai_infra.terraform.state import save_state

# ---------------------------------------------------------------------------
# Role → friendly label mapping
# ---------------------------------------------------------------------------

ROLE_LABELS: dict[str, str] = {
    "docker": "Docker Engine",
    "awscli": "AWS CLI",
    "python": "Python Environment",
    "ibl_cli_ops": "iblai-cli-ops",
    "ibl_platform": "Platform Config",
    "smtp_config": "SMTP Config",
    "ibl_dm": "iblai-dm-pro",
    "ibl_edx": "iblai-edx-pro",
    "ibl_spa": "SPA Services",
    "integrations": "OAuth & Integrations",
    "admin_setup": "Admin & CORS Setup",
    "data_seeding": "Data Seeding",
    "stripe_config": "Stripe Config",
}

LAUNCH_ROLE_LABELS: dict[str, str] = {
    "ibl_cli_ops": "iblai-cli-ops",
    "ibl_launch": "AMI Launch Config",
    "smtp_config": "SMTP Config",
    "ibl_launch_services": "Service Restart",
    "integrations": "OAuth & Integrations",
    "admin_setup": "Admin & CORS Setup",
    "data_seeding": "Data Seeding",
    "stripe_config": "Stripe Config",
}

SERVICE_UPDATE_ROLE_LABELS: dict[str, str] = {
    "ibl_cli_ops": "iblai-cli-ops",
    "ibl_service_update": "Service Update",
}

CALL_ROLE_LABELS: dict[str, str] = {
    "docker": "Docker Engine",
    "awscli": "AWS CLI",
    "python": "Python Environment",
    "ibl_cli_ops": "iblai-cli-ops",
    "ibl_call": "Call Stack (LiveKit)",
}

TOTAL_ROLES = len(ROLE_LABELS)

# Regex to match TASK lines: "TASK [role_name : task description]"
_TASK_RE = re.compile(r"^TASK\s+\[(.+?)\]")
# Regex to match fatal/failed lines
_FATAL_RE = re.compile(r"^(fatal|FAILED!)", re.IGNORECASE)


class AnsibleRunner:
    """Manages Ansible workspace, playbook execution, and progress tracking."""

    # Class-level defaults so __new__() (used in tests) doesn't break
    playbook: str = "playbook.yml"
    role_labels: dict[str, str] = ROLE_LABELS

    def __init__(
        self,
        state: ProjectState,
        config: SetupConfig,
        playbook: str = "playbook.yml",
        role_labels: dict[str, str] | None = None,
    ):
        self.state = state
        self.config = config
        self.ws = Path(state.workspace_path) / "ansible"
        self.playbook = playbook
        self.role_labels = role_labels or ROLE_LABELS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preflight(self) -> bool:
        """Run pre-flight checks. Returns True if all passed."""
        if not self._check_ansible_installed():
            return False
        if not self._test_ssh():
            return False
        return True

    def setup(self) -> None:
        """Copy templates and generate inventory."""
        self._copy_templates()
        self._generate_inventory()
        ui.success(f"Ansible workspace ready  [muted]{self.ws}[/muted]")

    def run(self) -> bool:
        """Run ansible-playbook with all roles."""
        from datetime import datetime, timezone

        self.state.setup_status = "running"
        save_state(self.state)

        ui.newline()

        # Build step tracking
        steps: dict[str, dict] = {}
        for name, label in self.role_labels.items():
            steps[name] = {"label": label, "status": "pending", "elapsed": 0}

        total_roles = len(self.role_labels)
        completed = 0
        progress = ui.make_overall_progress()
        task_id = progress.add_task("Setting up platform", total=total_roles)

        ok, completed = self._run_ansible(steps, progress, task_id, completed)

        self._print_final_table(steps)

        if not ok:
            self.state.setup_status = "failed"
            self.state.updated_at = datetime.now(timezone.utc)
            save_state(self.state)
            ui.newline()
            ui.error("Setup failed. Fix the issue and re-run [brand]iblai infra setup[/brand]")
            ui.newline()
            return False

        self.state.setup_status = "completed"
        self.state.setup_completed_at = datetime.now(timezone.utc)
        self.state.updated_at = datetime.now(timezone.utc)
        save_state(self.state)

        ui.success(f"[highlight]{completed}[/highlight] of {len(self.role_labels)} steps completed")
        return True

    # ------------------------------------------------------------------
    # Ansible execution
    # ------------------------------------------------------------------

    def _run_ansible(
        self,
        steps: dict[str, dict],
        progress: ui.Progress,
        task_id: int,
        completed: int,
    ) -> tuple[bool, int]:
        """Run ansible-playbook and track progress. Returns (success, completed)."""
        from collections import deque

        errors: list[str] = []
        output_tail: deque[str] = deque(maxlen=30)
        # Lines a role asked us to surface to the operator after the run
        # completes — captured between IBLAI_FIXTURE_OUTPUT_BEGIN/END markers
        # in a debug task's `msg`. Used by playwright_test_platforms to show
        # the regenerated test-user password without persisting it to disk.
        fixture_output: list[str] = []
        current_role: str | None = None
        role_start_time: float = 0

        extra_vars = self._build_extra_vars()

        cmd = [
            "ansible-playbook",
            self.playbook,
            "--extra-vars", json.dumps(extra_vars),
        ]

        proc = subprocess.Popen(
            cmd,
            cwd=self.ws,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=self._env(),
        )

        is_ci = os.environ.get("CI", "").lower() in ("true", "1")

        if is_ci:
            # CI mode: plain text output, no Rich Live display
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    output_tail.append(line)

                self._maybe_capture_fixture(line, fixture_output)
                role_name, task_desc = self._extract_role_and_task(line)
                if role_name and role_name in steps and task_desc:
                    steps[role_name]["task"] = task_desc
                if role_name and role_name != current_role:
                    if current_role and current_role in steps:
                        elapsed = int(time.time() - role_start_time)
                        steps[current_role]["status"] = "complete"
                        steps[current_role]["elapsed"] = elapsed
                        completed += 1
                        progress.update(task_id, completed=completed)
                        label = self.role_labels.get(current_role, current_role)
                        print(f"  ✓ {label} — done ({elapsed}s)", flush=True)

                    current_role = role_name
                    role_start_time = time.time()
                    if role_name in steps:
                        steps[role_name]["status"] = "in_progress"
                        label = self.role_labels.get(role_name, role_name)
                        task_info = f" — {task_desc}" if task_desc else ""
                        print(f"  ● {label}{task_info}...", flush=True)
                elif role_name and role_name == current_role and task_desc:
                    label = self.role_labels.get(role_name, role_name)
                    print(f"    ↳ {task_desc}", flush=True)

                if _FATAL_RE.match(line):
                    errors.append(line.strip())
                    if current_role and current_role in steps:
                        steps[current_role]["status"] = "error"
                    print(f"  ✗ {line.strip()}", flush=True)

                if current_role and current_role in steps and steps[current_role]["status"] == "in_progress":
                    steps[current_role]["elapsed"] = int(time.time() - role_start_time)

            proc.wait()
        else:
            # Interactive mode: Rich Live display
            with Live(
                self._build_display(steps, progress),
                console=ui.console,
                refresh_per_second=4,
                transient=True,
            ) as live:
                for line in proc.stdout:
                    line = line.rstrip()

                    if line:
                        output_tail.append(line)

                    self._maybe_capture_fixture(line, fixture_output)
                    role_name, task_desc = self._extract_role_and_task(line)
                    if role_name and role_name in steps and task_desc:
                        steps[role_name]["task"] = task_desc
                    if role_name and role_name != current_role:
                        if current_role and current_role in steps:
                            steps[current_role]["status"] = "complete"
                            steps[current_role]["elapsed"] = int(time.time() - role_start_time)
                            completed += 1
                            progress.update(task_id, completed=completed)

                        current_role = role_name
                        role_start_time = time.time()
                        if role_name in steps:
                            steps[role_name]["status"] = "in_progress"

                    if _FATAL_RE.match(line):
                        errors.append(line.strip())
                        if current_role and current_role in steps:
                            steps[current_role]["status"] = "error"

                    if current_role and current_role in steps and steps[current_role]["status"] == "in_progress":
                        steps[current_role]["elapsed"] = int(time.time() - role_start_time)

                    live.update(self._build_display(steps, progress))

                proc.wait()

        # Mark last role complete
        if current_role and current_role in steps and steps[current_role]["status"] == "in_progress":
            steps[current_role]["status"] = "complete"
            steps[current_role]["elapsed"] = int(time.time() - role_start_time)
            completed += 1
            progress.update(task_id, completed=completed)

        # Surface any fixture output captured during the run (e.g. the
        # Playwright test-fixture password block). The Rich Live display in
        # interactive mode runs with transient=True, so we print these AFTER
        # the Live has been torn down — that's the point at which the
        # captured lines actually become visible to the operator. Always
        # print on success; on failure the regular error block already shows
        # the last 30 lines of output, which is more useful than partial
        # fixture output that may not have completed.
        if fixture_output and proc.returncode == 0:
            ui.newline()
            ui.console.rule("[bold yellow]Captured fixture output (one-time, save it now)[/]")
            for ln in fixture_output:
                ui.console.print(ln)
            ui.console.rule()
            ui.newline()

        if proc.returncode != 0:
            if errors:
                ui.error("Ansible reported the following errors:")
                ui.newline()
                for e in errors[:5]:
                    ui.muted(f"  {e}")
            else:
                ui.error(f"ansible-playbook exited with code {proc.returncode}")

            if output_tail:
                ui.newline()
                ui.error("Last lines of output:")
                ui.newline()
                for tail_line in output_tail:
                    ui.muted(f"  {tail_line}")

            return False, completed

        # Warn about ignored errors but don't fail the run
        if errors:
            ui.warning(
                f"{len(errors)} task(s) failed but were ignored by the playbook:"
            )
            for e in errors[:5]:
                ui.muted(f"  {e}")

        return True, completed

    # ------------------------------------------------------------------
    # Pre-flight checks
    # ------------------------------------------------------------------

    def _check_ansible_installed(self) -> bool:
        if shutil.which("ansible-playbook") is None:
            ui.error("ansible-playbook not found")
            ui.newline()
            ui.info("Install with: [highlight]pip install ansible-core[/highlight]")
            ui.muted("Then re-run: [brand]iblai infra setup " + self.state.name + "[/brand]")
            ui.newline()
            return False
        return True

    def _test_ssh(self) -> bool:
        """Test SSH connectivity to the target host with retries."""
        max_retries = 10
        delay = 15

        for attempt in range(1, max_retries + 1):
            ui.info(f"Testing SSH connection ({attempt}/{max_retries})...")
            result = subprocess.run(
                [
                    "ssh",
                    "-o", "ConnectTimeout=15",
                    "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=no",
                    "-i", str(self.config.ssh_private_key_path),
                    f"{self.config.ssh_user}@{self.config.target_host}",
                    "true",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                ui.success(f"SSH connection verified ({self.config.target_host})")
                return True

            stderr = result.stderr.strip().lower() if result.stderr else ""

            # Permission denied means SSH is up but key is wrong — don't retry
            if "permission denied" in stderr:
                ui.error(f"Cannot connect to [highlight]{self.config.target_host}[/highlight] via SSH")
                ui.newline()
                ui.muted("  The SSH key may not match the key pair used during provisioning.")
                ui.muted(f"  Key used: {self.config.ssh_private_key_path}")
                ui.newline()
                return False

            # Retryable errors — instance still booting
            if attempt < max_retries:
                ui.muted(f"  SSH not ready, retrying in {delay}s...")
                time.sleep(delay)

        ui.error(f"Cannot connect to [highlight]{self.config.target_host}[/highlight] via SSH after {max_retries} attempts")
        ui.newline()

        stderr = result.stderr.strip().lower() if result.stderr else ""
        if "connection refused" in stderr or "connection timed out" in stderr:
            ui.muted("  The instance may still be starting, or port 22 is not open.")
            ui.muted("  Check your security group allows SSH from the runner's IP.")
        elif "no route to host" in stderr:
            ui.muted("  The IP address may be unreachable. Verify the instance is running.")
        else:
            ui.muted(f"  {result.stderr.strip()}")

        ui.newline()
        return False

    # ------------------------------------------------------------------
    # Workspace setup
    # ------------------------------------------------------------------

    def _topology(self) -> str:
        """Pick the ansible template topology based on state.config.deployment_type.

        Falls back to 'single-server' when state has no config (e.g. when
        AnsibleRunner is built from a bootstrap ProjectState that pre-dates
        the deployment_type field).
        """
        try:
            return self.state.config.deployment_type.value
        except AttributeError:
            return "single-server"

    def _host_group(self) -> str:
        """Inventory host group name for this deployment type."""
        return "call_servers" if self._topology() == DeploymentType.CALL.value else "ibl_servers"

    def _copy_templates(self) -> None:
        """Copy Ansible template files to workspace."""
        topology = self._topology()
        template_dir = Path(__file__).parent / "templates" / topology
        if not template_dir.exists():
            # Fall back to single-server for topologies that don't ship their own
            # ansible template (e.g. "multi-server" currently reuses single-server).
            template_dir = Path(__file__).parent / "templates" / "single-server"
        if not template_dir.exists():
            ui.abort(f"Ansible template directory not found: {template_dir}")

        if self.ws.exists():
            shutil.rmtree(self.ws)

        shutil.copytree(template_dir, self.ws)

    def _generate_inventory(self) -> None:
        """Generate inventory.ini from SetupConfig."""
        group = self._host_group()
        content = (
            f"[{group}]\n"
            f"{self.config.target_host}"
            f" ansible_user={self.config.ssh_user}"
            f" ansible_ssh_private_key_file={self.config.ssh_private_key_path}\n"
            "\n"
            f"[{group}:vars]\n"
            "ansible_python_interpreter=/usr/bin/python3\n"
        )
        (self.ws / "inventory.ini").write_text(content)

    def _build_extra_vars(self) -> dict:
        """Build the extra-vars dict. Secrets are passed here, never to disk."""
        cli_ops_repo, cli_ops_subdir = parse_repo_path(self.config.cli_ops_repo)
        prod_images_repo, prod_images_subdir = parse_repo_path(self.config.prod_images_repo)
        extra = {
            "aws_access_key_id": self.config.aws_access_key_id,
            "aws_secret_access_key": self.config.aws_secret_access_key,
            "aws_default_region": self.config.aws_default_region,
            "git_access_token": self.config.git_access_token,
            "github_org": self.config.github_org,
            "cli_ops_repo": cli_ops_repo,
            "cli_ops_subdir": cli_ops_subdir or "",
            "prod_images_repo": prod_images_repo,
            "prod_images_subdir": prod_images_subdir or "",
            "base_domain": self.config.base_domain,
            "edx_version": self.config.edx_version,
            "env_config": self.config.env_config,
            "cli_ops_release_tag": self.config.cli_ops_release_tag,
            "prod_images_tag": self.config.prod_images_tag,
            "is_resetup": self.config.is_resetup,
            "enable_ai": self.config.enable_ai,
            "create_playwright_platforms": self.config.create_playwright_platforms,
            "smtp_enabled": self.config.smtp_enabled,
            "smtp_host": self.config.smtp_host,
            "smtp_port": self.config.smtp_port,
            "smtp_username": self.config.smtp_username,
            "smtp_password": self.config.smtp_password,
            "smtp_sender_email": self.config.smtp_sender_email,
            "smtp_use_tls": self.config.smtp_use_tls,
            "smtp_use_ssl": self.config.smtp_use_ssl,
            "stripe_enabled": self.config.stripe_enabled,
            "stripe_mode": self.config.stripe_mode,
            "stripe_secret_key": self.config.stripe_secret_key,
            "stripe_pub_key": self.config.stripe_pub_key,
            "stripe_pricing_table_id": self.config.stripe_pricing_table_id,
            "stripe_pricing_table_id_returning": self.config.stripe_pricing_table_id_returning,
            "stripe_webhook_secret": self.config.stripe_webhook_secret,
            "stripe_connect_webhook_secret": self.config.stripe_connect_webhook_secret,
            "openai_api_key": self.config.openai_api_key,
            "admin_username": self.config.admin_username,
            "admin_email": self.config.admin_email,
            "admin_password": self.config.admin_password,
        }
        return extra

    # ------------------------------------------------------------------
    # Live display
    # ------------------------------------------------------------------

    def _build_display(
        self,
        steps: dict[str, dict],
        progress: ui.Progress,
    ) -> Group:
        table = self._build_role_table(steps)
        return Group(
            ui.section_group("Setting Up Platform", table),
            progress,
        )

    @staticmethod
    def _build_role_table(roles: dict[str, dict]) -> Table:
        table = Table(
            show_header=True,
            header_style=f"bold {ui.IBL_BLUE_LIGHT}",
            border_style=ui.IBL_NAVY,
            padding=(0, 1),
            expand=False,
            min_width=50,
        )
        table.add_column("Component", style="white", min_width=28)
        table.add_column("Status", min_width=14, justify="center")
        table.add_column("Time", justify="right", min_width=6, style=ui.IBL_BLUE_PALE)

        for info in roles.values():
            status = info.get("status", "pending")
            elapsed = info.get("elapsed", 0)

            if status == "complete":
                status_display = "[bold #3ECF6E]\u2713 Done[/]"
            elif status == "in_progress":
                task_desc = info.get("task", "")
                if task_desc:
                    # Truncate long descriptions
                    if len(task_desc) > 40:
                        task_desc = task_desc[:37] + "..."
                    status_display = f"[bold {ui.IBL_BLUE_LIGHT}]\u25cf {task_desc}[/]"
                else:
                    status_display = f"[bold {ui.IBL_BLUE_LIGHT}]\u25cf Running[/]"
            elif status == "error":
                status_display = "[bold #E85454]\u2717 Failed[/]"
            else:
                status_display = "[dim]\u25cb Pending[/dim]"

            time_display = f"{elapsed}s" if elapsed else "\u2014"
            table.add_row(info["label"], status_display, time_display)

        return table

    def _print_final_table(self, steps: dict[str, dict]) -> None:
        if not steps:
            return
        table = self._build_role_table(steps)
        ui.section("Setup Results", table)

    # ------------------------------------------------------------------
    # Line-based output parsing (ansible default callback)
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_capture_fixture(line: str, sink: list[str]) -> None:
        """Capture content between IBLAI_FIXTURE_OUTPUT_BEGIN/END markers.

        A role can ask the runner to surface text to the operator's terminal
        (visible AFTER the transient Live display is torn down) by emitting
        a `debug: msg=...` containing the begin/end markers. The default
        ansible callback JSON-encodes multi-line msg into a single stdout
        line with `\\n` escapes, so both markers land on the same line; we
        match that single-line shape and decode the embedded escapes.

        Used by playwright_test_platforms to display the regenerated
        test-user password without persisting it to disk.
        """
        BEGIN = "IBLAI_FIXTURE_OUTPUT_BEGIN"
        END = "IBLAI_FIXTURE_OUTPUT_END"
        if BEGIN not in line or END not in line:
            return
        try:
            start = line.index(BEGIN) + len(BEGIN)
            end = line.index(END)
            if end <= start:
                return
            raw = line[start:end]
            # Decode JSON-style \n / \" escapes from the default callback.
            # Wrap in quotes so json.loads gives us the unescaped string.
            decoded = json.loads(f'"{raw}"') if raw else ""
        except (ValueError, json.JSONDecodeError):
            return
        for sub in decoded.splitlines():
            stripped = sub.strip()
            if stripped:
                sink.append(stripped)

    def _extract_role_and_task(self, line: str) -> tuple[str | None, str | None]:
        """Extract role name and task description from an Ansible TASK line.

        Ansible default output format:
            TASK [role_name : task description] ***
            TASK [task description] ***  (for pre_tasks without a role)

        Returns (role_name, task_description) or (None, None).
        """
        match = _TASK_RE.match(line)
        if not match:
            return None, None

        task_label = match.group(1)

        if " : " in task_label:
            role_part, task_desc = task_label.split(" : ", 1)
            role_part = role_part.strip()
            task_desc = task_desc.strip()
            if role_part in self.role_labels:
                return role_part, task_desc

        label_lower = task_label.lower()
        for role_name in self.role_labels:
            if role_name in label_lower:
                return role_name, task_label.strip()

        return None, None

    def _extract_role_from_line(self, line: str) -> str | None:
        """Extract role name from an Ansible TASK line."""
        role, _ = self._extract_role_and_task(line)
        return role

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        """Build environment for ansible-playbook subprocess."""
        env = os.environ.copy()
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        env["ANSIBLE_FORCE_COLOR"] = "false"
        env["ANSIBLE_CONFIG"] = str(self.ws / "ansible.cfg")
        return env
