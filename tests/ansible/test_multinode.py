"""Multi-node runner and playbook templates.

Nothing here touches a server: the workspace is built in tmp_path, the
inventory is parsed by `ansible-inventory`, and the playbooks are checked
with `ansible-playbook --syntax-check`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from iblai_infra.ansible import multinode as mn
from iblai_infra.ansible.runner import AnsibleRunner
from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DeploymentType,
    DNSConfig,
    Environment,
    InfraConfig,
    MultiNodeTopology,
    NetworkConfig,
    ProjectState,
    SetupConfig,
    SSHConfig,
    SSHKeyMethod,
)

TEMPLATES = Path(__file__).resolve().parents[2] / "src/iblai_infra/ansible/templates"
MN = TEMPLATES / "multi-node"
PLAYBOOKS = ["app_init", "stores_setup", "app_setup", "app_add", "sync", "sandbox_setup",
             "sandbox_connect", "call_connect", "features", "verify"]
ANSIBLE_BIN = Path(shutil.which("ansible-playbook") or "").parent


@pytest.fixture
def topology() -> MultiNodeTopology:
    t = MultiNodeTopology(hub="10.0.1.10", apps=["10.0.1.11"], stores="10.0.2.10",
                          sandbox="10.0.3.10", node_id_prefix="demo")
    t.assign_node_ids()
    return t


@pytest.fixture
def state(tmp_path) -> ProjectState:
    return ProjectState(
        name="demo",
        provider="multi-node",
        status="created",
        config=InfraConfig(
            project_name="demo",
            environment=Environment.PROD,
            deployment_type=DeploymentType.MULTI_NODE,
            credentials=AWSCredentials(method=AuthMethod.ACCESS_KEY, region="us-east-1"),
            network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="0.0.0.0"),
            compute=ComputeConfig(),
            ssh=SSHConfig(method=SSHKeyMethod.EXISTING_FILE, key_name="k", private_key_path=tmp_path / "key"),
            certificates=CertificateConfig(method=CertMethod.NONE),
            dns=DNSConfig(base_domain="example.com"),
        ),
        outputs={"instance_public_ip": "10.0.1.10"},
        workspace_path=str(tmp_path / "ws"),
    )


@pytest.fixture
def config(tmp_path) -> SetupConfig:
    return SetupConfig(
        ssh_private_key_path=tmp_path / "key", target_host="10.0.1.10", base_domain="example.com",
        aws_access_key_id="", aws_secret_access_key="", aws_default_region="us-east-1", git_access_token="",
    )


def _runner(state, config, topology, playbook="sync.yml", **kw) -> mn.MultiNodeRunner:
    return mn.MultiNodeRunner(state, config, topology, playbook, mn.SYNC_LABELS, **kw)


def _inventory(runner) -> dict:
    runner._copy_templates()
    runner._generate_inventory()
    out = subprocess.run(
        [str(ANSIBLE_BIN / "ansible-inventory"), "-i", str(runner.ws / "inventory.ini"), "--list"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def _hostvars(inv: dict, host: str) -> dict:
    return inv["_meta"]["hostvars"][host]


class TestWorkspace:
    def test_shared_roles_are_copied_beside_the_multi_node_roles(self, state, config, topology):
        r = _runner(state, config, topology)
        r._copy_templates()
        assert (r.ws / "roles" / "mn_hub_config").is_dir()
        assert (r.ws / "shared_roles" / "ibl_dm" / "tasks" / "local_db.yml").is_file()
        assert "roles_path = ./roles:./shared_roles" in (r.ws / "ansible.cfg").read_text()

    def test_single_server_runner_is_unchanged(self, project_state, setup_config, tmp_path):
        """No shared_roles, no multi-node groups: the base runner never sees this feature."""
        project_state.workspace_path = str(tmp_path / "single")
        r = AnsibleRunner(project_state, setup_config)
        r._copy_templates()
        r._generate_inventory()
        assert not (r.ws / "shared_roles").exists()
        assert (r.ws / "inventory.ini").read_text().startswith("[ibl_servers]\n")


@pytest.mark.skipif(not ANSIBLE_BIN.name, reason="ansible not installed")
class TestInventory:
    def test_groups_and_node_ids(self, state, config, topology):
        inv = _inventory(_runner(state, config, topology))
        assert inv["hub"]["hosts"] == ["10.0.1.10"]
        assert inv["apps"]["hosts"] == ["10.0.1.11"]
        assert set(inv["app_nodes"]["children"]) == {"hub", "apps"}
        assert inv["stores"]["hosts"] == ["10.0.2.10"]
        assert inv["sandbox"]["hosts"] == ["10.0.3.10"]
        assert _hostvars(inv, "10.0.2.10")["node_id"] == "demo-stores"
        assert _hostvars(inv, "10.0.1.11")["node_id"] == "demo-app-2"

    def test_new_apps_join_apps_too(self, state, config, topology):
        inv = _inventory(_runner(state, config, topology, new_apps=["10.0.1.12"]))
        assert inv["new_apps"]["hosts"] == ["10.0.1.12"]
        assert "10.0.1.12" in inv["apps"]["hosts"]

    def test_jump_host_becomes_a_proxy_command(self, state, config, topology):
        topology.ssh_jump_host = "203.0.113.5"
        inv = _inventory(_runner(state, config, topology))
        args = _hostvars(inv, "10.0.2.10")["ansible_ssh_common_args"]
        assert args.startswith("-o ProxyCommand=")
        assert "-W %h:%p ubuntu@203.0.113.5" in args

    def test_call_host_keeps_its_own_key(self, state, config, topology, tmp_path):
        extra = mn.ExtraHost("198.51.100.7", "call", tmp_path / "call-key")
        topology.ssh_jump_host = "203.0.113.5"
        inv = _inventory(_runner(state, config, topology, extra_hosts=[extra]))
        hv = _hostvars(inv, "198.51.100.7")
        assert hv["ansible_ssh_private_key_file"] == str(tmp_path / "call-key")
        assert "ansible_ssh_common_args" not in hv


class TestExtraVars:
    def test_node_id_comes_from_the_inventory(self, state, config, topology):
        """extra-vars outrank host vars, so a run-wide node_id would give every
        node the same NODE_ID."""
        r = _runner(state, config, topology, run_vars={"postgres_location": "managed"})
        extra = r._build_extra_vars()
        assert "node_id" not in extra
        assert extra["postgres_location"] == "managed"
        assert extra["base_domain"] == "example.com"


class TestRunStep:
    def test_only_app_setup_marks_setup_complete(self, state, config, topology, monkeypatch, workspace_root):
        r = _runner(state, config, topology)
        monkeypatch.setattr(r, "_run_ansible", lambda *a: (True, 1))
        monkeypatch.setattr(r, "_print_final_table", lambda *a: None)
        assert r.run_step("x")
        assert state.setup_status is None
        assert r.run_step("x", completes_setup=True)
        assert state.setup_status == "completed"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "mnws"
    shutil.copytree(MN, ws)
    shutil.copytree(TEMPLATES / "single-server" / "roles", ws / "shared_roles")
    (ws / "inventory.ini").write_text(
        "[hub]\n10.0.1.10 node_id=a\n[apps]\n[new_apps]\n[stores]\n10.0.2.10 node_id=s\n"
        "[sandbox]\n10.0.3.10 node_id=x\n[call]\n10.0.4.10\n[app_nodes:children]\nhub\napps\n"
    )
    return ws


def _syntax_check(ws: Path, playbook: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ANSIBLE_BIN / "ansible-playbook"), "--syntax-check", playbook],
        cwd=ws, capture_output=True, text=True, env={"ANSIBLE_CONFIG": str(ws / "ansible.cfg"), "PATH": "/usr/bin:/bin"},
    )


@pytest.mark.skipif(not ANSIBLE_BIN.name, reason="ansible not installed")
class TestSyntax:
    @pytest.mark.parametrize("playbook", PLAYBOOKS)
    def test_playbook(self, tmp_path, playbook):
        ws = _workspace(tmp_path)
        result = _syntax_check(ws, f"{playbook}.yml")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_every_role_task_file(self, tmp_path):
        """Playbooks reach most role files through include_role, which a syntax
        check doesn't load; importing each one statically does."""
        ws = _workspace(tmp_path)
        plays = [
            {"hosts": "hub", "gather_facts": False,
             "tasks": [{"import_role": {"name": f.parts[-3], "tasks_from": f.stem}}]}
            for f in sorted((ws / "roles").glob("*/tasks/*.yml"))
        ]
        (ws / "all_roles.yml").write_text(yaml.safe_dump(plays))
        result = _syntax_check(ws, "all_roles.yml")
        assert result.returncode == 0, result.stdout + result.stderr


def _role_tasks(role: str, file: str = "main.yml") -> list[dict]:
    return yaml.safe_load((MN / "roles" / role / "tasks" / file).read_text())


def _all_mn_tasks():
    for f in sorted((MN / "roles").glob("*/tasks/*.yml")):
        for task in yaml.safe_load(f.read_text()) or []:
            yield f, task
            for inner in task.get("block", []):
                yield f, inner


def _shell(task: dict) -> str:
    sh = task.get("shell", task.get("ansible.builtin.shell")) or ""
    return sh.get("cmd", "") if isinstance(sh, dict) else sh


class TestSecretHandling:
    def test_secret_reads_are_never_logged(self):
        """A task whose output holds a secret value must be no_log. (A key
        captured into a shell variable, as the smoke test does, never reaches
        the output.)"""
        secret_reads = re.compile(
            r"ibl config get (IBL_EDX\.MONGODB_PASSWORD|IBL_CALL\.LIVEKIT_API_(KEY|SECRET)|.*DB_PASSWORD)"
            r"|(?<!=\$\()ibl sandbox issue-token|base64 -w0"
        )
        for f, task in _all_mn_tasks():
            if secret_reads.search(_shell(task)) or "slurp" in task:
                assert task.get("no_log") is True, f"{f}: {task['name']}"

    def test_secrets_reach_ibl_through_the_environment(self):
        """`ibl secrets set` reads values with --from-env, never from argv."""
        for f, task in _all_mn_tasks():
            for line in _shell(task).splitlines():
                if "ibl secrets set" in line:
                    assert "--from-env" in line and not re.search(r"set [A-Z_.]+=\S", line), f"{f}: {task['name']}"

    def test_no_hardcoded_addresses(self):
        """Every address comes from the inventory or the operator's .env."""
        ip = re.compile(r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)\d{1,3}(\.\d{1,3}){3}\b")
        for f in MN.rglob("*"):
            if f.is_file():
                assert not ip.search(f.read_text()), f


class TestPlaybookShape:
    def _roles(self, playbook: str, play: int) -> list[str]:
        plays = yaml.safe_load((MN / playbook).read_text())
        return [r if isinstance(r, str) else r["role"] for r in plays[play].get("roles", [])]

    def test_every_playbook_gathers_facts_on_all_hosts_first(self):
        for name in PLAYBOOKS:
            if name == "sandbox_setup":
                continue
            plays = yaml.safe_load((MN / f"{name}.yml").read_text())
            assert plays[0] == {"ansible.builtin.import_playbook": "facts.yml"}, name

    def test_hub_never_runs_a_local_dm_database(self):
        plays = yaml.safe_load((MN / "app_setup.yml").read_text())
        assert plays[1]["vars"]["dm_db_local"] is False

    def test_dm_migrates_before_edx_and_integrations_follow(self):
        roles = self._roles("app_setup.yml", 1)
        assert roles.index("ibl_dm") < roles.index("ibl_edx") < roles.index("integrations")

    def test_beat_starts_after_the_hub_migrates(self):
        plays = yaml.safe_load((MN / "app_setup.yml").read_text())
        assert plays[-1]["hosts"] == "stores" and plays[-1]["roles"] == ["mn_beat"]

    def test_rolling_apply_is_one_node_at_a_time(self):
        plays = yaml.safe_load((MN / "sync.yml").read_text())
        apply = next(p for p in plays if p.get("roles") == ["mn_apply"])
        assert apply["serial"] == 1

    def test_stores_node_never_starts_the_application(self):
        for f in (MN / "roles" / "mn_stores_node" / "tasks").glob("*.yml"):
            for task in yaml.safe_load(f.read_text()):
                sh = _shell(task)
                assert not re.search(r"\bibl dm (up|update)\b|\bibl edx (start|launch)\b", sh), task["name"]
                if "tutor local start" in sh:
                    assert re.search(r"tutor local start -d \{\{", sh), "tutor must name its services"

    def test_beat_starts_without_dependencies(self):
        sh = _shell(_role_tasks("mn_beat")[0])
        assert "--no-deps celery_beat" in sh

    def test_labels_name_roles_their_playbooks_run(self):
        role_names = {p.name for p in (MN / "roles").iterdir()} | {
            p.name for p in (TEMPLATES / "single-server" / "roles").iterdir()}
        for labels in (mn.APP_INIT_LABELS, mn.STORES_SETUP_LABELS, mn.APP_SETUP_LABELS, mn.APP_ADD_LABELS,
                       mn.SYNC_LABELS, mn.SANDBOX_SETUP_LABELS, mn.SANDBOX_CONNECT_LABELS,
                       mn.CALL_CONNECT_LABELS, mn.VERIFY_LABELS):
            assert set(labels) <= role_names


class TestSharedRoleDefaults:
    """The single-server roles gained switches for multi-node; their defaults
    must keep single-server behaviour exactly as it was."""

    def _ss(self, role: str, file: str = "main.yml") -> list[dict]:
        return yaml.safe_load((TEMPLATES / "single-server" / "roles" / role / "tasks" / file).read_text())

    def test_dm_database_tasks_run_by_default(self):
        first = self._ss("ibl_dm")[0]
        assert first["ansible.builtin.import_tasks"] == "local_db.yml"
        assert first["when"] == "dm_db_local | default(true) | bool"

    def test_timescaledb_stays_on_by_default(self):
        ext = next(t for t in self._ss("ibl_dm", "local_db.yml") if t["name"] == "Enable TimescaleDB extension")
        assert ext["when"] == "enable_timescaledb | default(true) | bool"
        views = next(t for t in self._ss("data_seeding") if t["name"] == "Setup TimescaleDB views")
        assert views["when"] == "enable_timescaledb | default(true) | bool"
        configure = next(t for t in self._ss("ibl_platform") if t["name"].startswith("Configure platform"))
        assert "'true' if (enable_timescaledb | default(true) | bool) else 'false'" in _shell(configure)

    def test_cli_ops_main_still_installs_then_inits_single_server(self):
        tasks = self._ss("ibl_cli_ops")
        assert tasks[0]["ansible.builtin.import_tasks"] == "install.yml"
        init = next(t for t in tasks if "ibl init" in _shell(t))
        assert "ibl init --from single-server" in _shell(init)
