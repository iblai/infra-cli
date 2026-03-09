"""Ansible execution wrapper with Rich Live progress display.

Runs ansible-playbook with JSON stdout callback for structured progress
parsing, combined with Rich Live + Progress for real-time visual feedback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from iblai_infra import ui
from iblai_infra.models import ProjectState, SetupConfig
from iblai_infra.terraform.state import save_state

# ---------------------------------------------------------------------------
# Role → friendly label mapping
# ---------------------------------------------------------------------------

ROLE_LABELS: dict[str, str] = {
    "docker": "Docker Engine",
    "awscli": "AWS CLI",
    "python": "Python Environment",
    "ibl_cli_ops": "IBL CLI Ops",
    "ibl_platform": "Platform Configuration",
}

TOTAL_ROLES = len(ROLE_LABELS)


class AnsibleRunner:
    """Manages Ansible workspace and playbook execution."""

    def __init__(self, state: ProjectState, config: SetupConfig):
        self.state = state
        self.config = config
        self.ws = Path(state.workspace_path) / "ansible"

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
        """Run ansible-playbook with live progress. Returns True on success."""
        from datetime import datetime, timezone

        self.state.setup_status = "running"
        save_state(self.state)

        ui.newline()
        roles: dict[str, dict] = {}
        for name, label in ROLE_LABELS.items():
            roles[name] = {"label": label, "status": "pending", "elapsed": 0}

        completed = 0
        errors: list[str] = []
        current_role: str | None = None
        role_start_time: float = 0

        progress = ui.make_overall_progress()
        task_id = progress.add_task("Bootstrapping platform", total=TOTAL_ROLES)

        extra_vars = self._build_extra_vars()

        cmd = [
            "ansible-playbook",
            "playbook.yml",
            "--extra-vars", json.dumps(extra_vars),
        ]

        env = self._env()

        proc = subprocess.Popen(
            cmd,
            cwd=self.ws,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

        with Live(
            self._build_display(roles, progress),
            console=ui.console,
            refresh_per_second=4,
            transient=True,
        ) as live:
            for line in proc.stdout:
                event = self._parse_json_line(line)
                if not event:
                    continue

                # Detect role transitions from plays/tasks
                role_name = self._extract_role(event)
                if role_name and role_name != current_role:
                    # Mark previous role as complete
                    if current_role and current_role in roles:
                        roles[current_role]["status"] = "complete"
                        roles[current_role]["elapsed"] = int(time.time() - role_start_time)
                        completed += 1
                        progress.update(task_id, completed=completed)

                    current_role = role_name
                    role_start_time = time.time()
                    if role_name in roles:
                        roles[role_name]["status"] = "in_progress"

                # Detect failures
                if self._is_failure(event):
                    msg = self._extract_error(event)
                    errors.append(msg)
                    if current_role and current_role in roles:
                        roles[current_role]["status"] = "error"

                # Update elapsed time for current role
                if current_role and current_role in roles and roles[current_role]["status"] == "in_progress":
                    roles[current_role]["elapsed"] = int(time.time() - role_start_time)

                live.update(self._build_display(roles, progress))

            proc.wait()

        # Mark last role as complete (if no errors)
        if current_role and current_role in roles and roles[current_role]["status"] == "in_progress":
            roles[current_role]["status"] = "complete"
            roles[current_role]["elapsed"] = int(time.time() - role_start_time)
            completed += 1
            progress.update(task_id, completed=completed)

        # Print final table
        self._print_final_table(roles)

        if proc.returncode != 0 or errors:
            self.state.setup_status = "failed"
            self.state.updated_at = datetime.now(timezone.utc)
            save_state(self.state)
            for e in errors:
                ui.error(e)
            if not errors:
                # Show stderr if no structured errors were captured
                stderr = proc.stderr.read() if proc.stderr else ""
                if stderr.strip():
                    for errline in stderr.strip().splitlines()[-10:]:
                        ui.error(errline)
            ui.newline()
            ui.error("Setup failed. Fix the issue and re-run [brand]iblai infra setup[/brand]")
            ui.newline()
            return False

        self.state.setup_status = "completed"
        self.state.setup_completed_at = datetime.now(timezone.utc)
        self.state.updated_at = datetime.now(timezone.utc)
        save_state(self.state)

        ui.success(f"[highlight]{completed}[/highlight] of {TOTAL_ROLES} roles completed")
        return True

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
        """Test SSH connectivity to the target host."""
        from rich.status import Status

        with Status("  [info]Testing SSH connection...[/info]", console=ui.console):
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

        ui.error(f"Cannot connect to [highlight]{self.config.target_host}[/highlight] via SSH")
        ui.newline()

        # Diagnose common issues
        stderr = result.stderr.strip().lower() if result.stderr else ""
        if "permission denied" in stderr:
            ui.muted("  The SSH key may not match the key pair used during provisioning.")
            ui.muted(f"  Key used: {self.config.ssh_private_key_path}")
        elif "connection refused" in stderr or "connection timed out" in stderr:
            ui.muted("  The instance may still be starting, or port 22 is not open to your IP.")
            ui.muted("  Check your security group allows SSH from your current IP address.")
        elif "no route to host" in stderr:
            ui.muted("  The IP address may be unreachable. Verify the instance is running.")
        else:
            ui.muted(f"  {result.stderr.strip()}")

        ui.newline()
        ui.muted(f"When resolved, re-run: [brand]iblai infra setup {self.state.name}[/brand]")
        ui.newline()
        return False

    # ------------------------------------------------------------------
    # Workspace setup
    # ------------------------------------------------------------------

    def _copy_templates(self) -> None:
        """Copy Ansible template files to workspace."""
        template_dir = Path(__file__).parent / "templates" / "single-server"
        if not template_dir.exists():
            ui.abort(f"Ansible template directory not found: {template_dir}")

        # Clear previous ansible workspace if exists
        if self.ws.exists():
            shutil.rmtree(self.ws)

        shutil.copytree(template_dir, self.ws)

    def _generate_inventory(self) -> None:
        """Generate inventory.ini from SetupConfig."""
        content = (
            "[ibl_servers]\n"
            f"{self.config.target_host}"
            f" ansible_user={self.config.ssh_user}"
            f" ansible_ssh_private_key_file={self.config.ssh_private_key_path}\n"
            "\n"
            "[ibl_servers:vars]\n"
            "ansible_python_interpreter=/usr/bin/python3\n"
        )
        (self.ws / "inventory.ini").write_text(content)

    def _build_extra_vars(self) -> dict:
        """Build the extra-vars dict. Secrets are passed here, never to disk."""
        return {
            "git_access_token": self.config.git_access_token,
            "aws_access_key_id": self.config.aws_access_key_id,
            "aws_secret_access_key": self.config.aws_secret_access_key,
            "aws_default_region": self.config.aws_default_region,
            "base_domain": self.config.base_domain,
            "edx_version": self.config.edx_version,
            "env_config": self.config.env_config,
        }

    # ------------------------------------------------------------------
    # Live display
    # ------------------------------------------------------------------

    def _build_display(
        self,
        roles: dict[str, dict],
        progress: ui.Progress,
    ) -> Group:
        table = self._build_role_table(roles)
        return Group(
            Panel(
                table,
                title="[brand]Bootstrapping Platform[/brand]",
                border_style="cyan",
                padding=(0, 1),
            ),
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
                status_display = f"[bold {ui.IBL_BLUE_LIGHT}]\u25cf Running[/]"
            elif status == "error":
                status_display = "[bold #E85454]\u2717 Failed[/]"
            else:
                status_display = "[dim]\u25cb Pending[/dim]"

            time_display = f"{elapsed}s" if elapsed else "\u2014"
            table.add_row(info["label"], status_display, time_display)

        return table

    def _print_final_table(self, roles: dict[str, dict]) -> None:
        if not roles:
            return
        table = self._build_role_table(roles)
        ui.console.print(
            Panel(table, title="[brand]Setup Results[/brand]", border_style="cyan", padding=(0, 1))
        )

    # ------------------------------------------------------------------
    # JSON output parsing
    # ------------------------------------------------------------------

    def _extract_role(self, event: dict) -> str | None:
        """Extract the role name from an ansible JSON event."""
        # ansible JSON callback nests data in "plays" and "tasks"
        # For task-level events, look at task.role or task path
        for key in ("play", "task"):
            obj = event.get(key, {})
            if isinstance(obj, dict):
                # task.role field
                role = obj.get("role", "")
                if role and role in ROLE_LABELS:
                    return role
                # task.name may start with "role : task_name"
                name = obj.get("name", "")
                for role_name in ROLE_LABELS:
                    if role_name in name.lower():
                        return role_name

        # Check in nested plays->tasks structure (stats/recap format)
        for play in event.get("plays", []):
            for task in play.get("tasks", []):
                task_info = task.get("task", {})
                role = task_info.get("role", "")
                if role and role in ROLE_LABELS:
                    return role

        return None

    def _is_failure(self, event: dict) -> bool:
        """Check if an event represents a task failure."""
        # Check stats for failures
        stats = event.get("stats", {})
        for host_stats in stats.values():
            if host_stats.get("failures", 0) > 0 or host_stats.get("unreachable", 0) > 0:
                return True

        # Check host results
        for play in event.get("plays", []):
            for task in play.get("tasks", []):
                for host_result in task.get("hosts", {}).values():
                    if host_result.get("failed", False) or host_result.get("unreachable", False):
                        return True

        return False

    def _extract_error(self, event: dict) -> str:
        """Extract error message from a failure event."""
        for play in event.get("plays", []):
            for task in play.get("tasks", []):
                task_name = task.get("task", {}).get("name", "unknown task")
                for host_result in task.get("hosts", {}).values():
                    if host_result.get("failed") or host_result.get("unreachable"):
                        msg = host_result.get("msg", "")
                        stderr = host_result.get("stderr", "")
                        detail = msg or stderr or "Unknown error"
                        return f"{task_name}: {detail}"

        # Fallback: check stats
        stats = event.get("stats", {})
        for host, host_stats in stats.items():
            failures = host_stats.get("failures", 0)
            unreachable = host_stats.get("unreachable", 0)
            if failures > 0:
                return f"{host}: {failures} task(s) failed"
            if unreachable > 0:
                return f"{host}: host unreachable"

        return "Unknown error"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        """Build environment for ansible-playbook subprocess."""
        env = os.environ.copy()
        env["ANSIBLE_STDOUT_CALLBACK"] = "json"
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        env["ANSIBLE_FORCE_COLOR"] = "false"
        env["ANSIBLE_CONFIG"] = str(self.ws / "ansible.cfg")
        return env

    @staticmethod
    def _parse_json_line(line: str) -> dict | None:
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
