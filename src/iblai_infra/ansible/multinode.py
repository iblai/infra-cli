"""Ansible runner for multi-node deployments.

A subclass, so single-server and call-server runs keep `AnsibleRunner`'s
exact behaviour. What differs here:

- the workspace is the ``multi-node`` template plus the single-server roles
  copied in as ``shared_roles/`` (its ``ansible.cfg`` puts both on
  ``roles_path``), so both topologies run the same platform code;
- the inventory has a group per role - ``hub``, ``apps``, ``app_nodes``
  (both), ``stores``, ``sandbox``, ``new_apps`` and ``call`` - each host
  carrying its NODE_ID, optionally reached through a jump host;
- SSH is checked on every host the run touches;
- a step's run never marks the project's setup as completed unless asked.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from iblai_infra import ui
from iblai_infra.ansible.runner import AnsibleRunner
from iblai_infra.models import MultiNodeTopology, ProjectState, SetupConfig
from iblai_infra.terraform.state import save_state

TEMPLATES = Path(__file__).parent / "templates"

APP_INIT_LABELS: dict[str, str] = {
    "mn_base": "Base Software",
    "mn_hub_config": "Stores & Services",
    "ibl_platform": "Platform Config",
}
STORES_SETUP_LABELS: dict[str, str] = {
    "mn_base": "Base Software",
    "mn_sync": "Copy Hub Root",
    "mn_stores_node": "Stores Node",
}
APP_SETUP_LABELS: dict[str, str] = {
    "mn_managed_pg": "Managed Postgres",
    "mn_compat": "Compatibility Overrides",
    "smtp_config": "SMTP Config",
    "ibl_dm": "iblai-dm-pro",
    "ibl_edx": "iblai-edx-pro",
    "ibl_spa": "SPA Services",
    "integrations": "OAuth & Integrations",
    "admin_setup": "Admin & CORS Setup",
    "data_seeding": "Data Seeding",
    "ibl_tenant_platform": "Tenant Platform",
    "stripe_config": "Stripe Config",
    "google_sso_config": "Google SSO Config",
    "microsoft_sso_config": "Microsoft SSO Config",
    "mn_apply": "Load Balancer Keep-alive",
    "mn_beat": "Celery Beat (stores node)",
}
APP_ADD_LABELS: dict[str, str] = {
    "mn_base": "Base Software",
    "mn_sync": "Copy Hub Root",
    "mn_clone": "Start Services",
    "mn_stores_node": "Stores Firewall",
}
SYNC_LABELS: dict[str, str] = {
    "mn_sync": "Copy Hub Root",
    "mn_stores_node": "Stores Node Config",
    "mn_beat": "Celery Beat",
    "mn_apply": "Rolling Apply",
}
SANDBOX_SETUP_LABELS: dict[str, str] = {
    "mn_base": "Base Software",
    "mn_sandbox": "Sandbox Server",
}
SANDBOX_CONNECT_LABELS: dict[str, str] = {
    "mn_sandbox_connect": "Tenant & DM Key",
    **SYNC_LABELS,
}
CALL_CONNECT_LABELS: dict[str, str] = {
    "mn_livekit": "LiveKit Credentials",
    **SYNC_LABELS,
}
VERIFY_LABELS: dict[str, str] = {
    "mn_verify": "Containers",
    "mn_apply": "Local Health",
    "mn_sync": "Secrets Match",
}


@dataclass
class ExtraHost:
    """A host outside the topology a run also needs (the call server)."""
    host: str
    group: str
    key_path: Path
    user: str = "ubuntu"


class MultiNodeRunner(AnsibleRunner):
    def __init__(
        self,
        state: ProjectState,
        config: SetupConfig,
        topology: MultiNodeTopology,
        playbook: str,
        role_labels: dict[str, str],
        *,
        run_vars: dict | None = None,
        new_apps: list[str] | None = None,
        extra_hosts: list[ExtraHost] | None = None,
        ssh_groups: tuple[str, ...] = ("hub",),
        fact_groups: tuple[str, ...] | None = None,
    ):
        super().__init__(state, config, playbook=playbook, role_labels=role_labels)
        self.topology = topology
        # Non-secret and secret per-run variables, merged over SetupConfig's.
        self.run_vars = run_vars or {}
        # Hosts only this run touches: `new_apps` for `app add`, a `call`
        # group host for `call connect`.
        self.new_apps = new_apps or []
        self.extra_hosts = extra_hosts or []
        # Groups whose hosts must answer SSH before the run starts.
        self.ssh_groups = ssh_groups
        # Groups whose facts the run reads (default: the SSH-checked ones).
        # Wider than ssh_groups where a group may not be built yet, e.g. the
        # app nodes when the stores firewall is first written.
        self.fact_groups = fact_groups or ssh_groups

    # ------------------------------------------------------------------
    # Running a step
    # ------------------------------------------------------------------

    def run_step(self, description: str, *, completes_setup: bool = False) -> bool:
        """Run the playbook. Only `app setup` passes ``completes_setup`` -
        the step after which the deployment is up and features can be added."""
        from datetime import datetime, timezone

        ui.newline()
        steps = {
            name: {"label": label, "status": "pending", "elapsed": 0}
            for name, label in self.role_labels.items()
        }
        progress = ui.make_overall_progress()
        task_id = progress.add_task(description, total=max(len(steps), 1))
        if completes_setup:
            self.state.setup_status = "running"
            save_state(self.state)

        ok, completed = self._run_ansible(steps, progress, task_id, 0)
        self._print_final_table(steps)

        if completes_setup:
            now = datetime.now(timezone.utc)
            self.state.setup_status = "completed" if ok else "failed"
            if ok:
                self.state.setup_completed_at = now
            self.state.updated_at = now
            save_state(self.state)
        return ok

    # ------------------------------------------------------------------
    # Workspace
    # ------------------------------------------------------------------

    def _copy_templates(self) -> None:
        if self.ws.exists():
            shutil.rmtree(self.ws)
        shutil.copytree(TEMPLATES / "multi-node", self.ws)
        shutil.copytree(TEMPLATES / "single-server" / "roles", self.ws / "shared_roles")

    def _ssh_common_args(self) -> str:
        if not self.topology.ssh_jump_host:
            return ""
        proxy = (
            f"ssh -o BatchMode=yes -o StrictHostKeyChecking=no -i {shlex.quote(str(self.config.ssh_private_key_path))} "
            f"-W %h:%p {self.topology.ssh_user}@{self.topology.ssh_jump_host}"
        )
        return f"-o ProxyCommand={shlex.quote(proxy)}"

    def _host_line(self, host: str, *, key: Path | None = None, user: str | None = None) -> str:
        parts = [host]
        node_id = self.topology.node_ids.get(host)
        if node_id:
            parts.append(f"node_id={node_id}")
        parts.append(f"ansible_user={user or self.topology.ssh_user}")
        parts.append(f"ansible_ssh_private_key_file={key or self.config.ssh_private_key_path}")
        common = self._ssh_common_args() if key is None else ""
        if common:
            parts.append(f"ansible_ssh_common_args={shlex.quote(common)}")
        return " ".join(parts)

    def _generate_inventory(self) -> None:
        t = self.topology
        apps = [*t.apps, *[h for h in self.new_apps if h not in t.apps]]
        groups: dict[str, list[str]] = {
            "hub": [self._host_line(t.hub)],
            "apps": [self._host_line(h) for h in apps],
            "new_apps": [self._host_line(h) for h in self.new_apps],
            "stores": [self._host_line(t.stores)],
            "sandbox": [self._host_line(t.sandbox)] if t.sandbox else [],
            "call": [],
        }
        for extra in self.extra_hosts:
            groups.setdefault(extra.group, []).append(
                self._host_line(extra.host, key=extra.key_path, user=extra.user)
            )
        lines: list[str] = []
        for group, hosts in groups.items():
            lines.append(f"[{group}]")
            lines.extend(hosts)
            lines.append("")
        lines += ["[app_nodes:children]", "hub", "apps", "", "[all:vars]",
                  "ansible_python_interpreter=/usr/bin/python3", ""]
        (self.ws / "inventory.ini").write_text("\n".join(lines))

    def _build_extra_vars(self) -> dict:
        extra = super()._build_extra_vars()
        # Each host's NODE_ID comes from the inventory; a run-wide value
        # would override it (extra-vars outrank host vars).
        extra.pop("node_id", None)
        groups = [*self.fact_groups, *(h.group for h in self.extra_hosts)]
        extra["mn_fact_hosts"] = ":".join(dict.fromkeys(groups))
        extra.update(self.run_vars)
        return extra

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    def _ssh_targets(self) -> list[tuple[str, Path, str, str]]:
        t = self.topology
        by_group = {
            "hub": [t.hub],
            "apps": list(t.apps),
            "new_apps": list(self.new_apps),
            "stores": [t.stores],
            "sandbox": [t.sandbox] if t.sandbox else [],
        }
        targets: list[tuple[str, Path, str, str]] = []
        for group in self.ssh_groups:
            for host in by_group.get(group, []):
                targets.append((host, self.config.ssh_private_key_path, t.ssh_user, self._ssh_common_args()))
        for extra in self.extra_hosts:
            targets.append((extra.host, extra.key_path, extra.user, ""))
        seen: set[str] = set()
        return [x for x in targets if not (x[0] in seen or seen.add(x[0]))]

    def _test_ssh(self) -> bool:
        for host, key, user, common in self._ssh_targets():
            cmd = ["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
                   "-o", "StrictHostKeyChecking=no", "-i", str(key)]
            cmd += shlex.split(common)
            cmd += [f"{user}@{host}", "true"]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            except subprocess.TimeoutExpired:
                result = None
            if result is None or result.returncode != 0:
                ui.error(f"Cannot reach [highlight]{host}[/highlight] over SSH")
                detail = (result.stderr.strip() if result else "timed out") or "no output"
                ui.muted(f"  {detail}")
                ui.newline()
                return False
            ui.success(f"SSH connection verified ({host})")
        return True
