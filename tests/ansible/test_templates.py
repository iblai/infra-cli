"""Ordering and selection rules the playbook templates depend on.

These read the role YAML: each rule, broken, only shows up on a server,
usually as the first `ibl render` failing mid-bootstrap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

TEMPLATES = Path(__file__).resolve().parents[2] / "src/iblai_infra/ansible/templates"


def _tasks(kind: str, role: str, file: str = "main.yml") -> list[dict]:
    return yaml.safe_load((TEMPLATES / kind / "roles" / role / "tasks" / file).read_text())


def _shell(task: dict) -> str:
    sh = task.get("shell", task.get("ansible.builtin.shell")) or ""
    return sh.get("cmd", "") if isinstance(sh, dict) else sh


def _first(tasks: list[dict], pattern: str) -> int:
    return next(i for i, t in enumerate(tasks) if re.search(pattern, _shell(t)))


class TestSecretsPreflight:
    @pytest.mark.parametrize("kind", ["single-server", "call-server"])
    def test_secrets_are_generated_before_the_preflight(self, kind):
        """A secret defaulting to another one reads as operator-supplied until
        that one is generated, so a preflight ahead of generate failed every
        fresh setup."""
        tasks = _tasks(kind, "ibl_cli_ops")
        assert _first(tasks, r"ibl secrets generate") < _first(tasks, r"ibl secrets check --json")


class TestAiCallGate:
    def test_ai_calls_are_settled_before_the_first_render(self):
        """With AI on, the preset's AI call service needs a call server's LiveKit
        key and secret; without them every render fails."""
        tasks = _tasks("single-server", "ibl_platform")
        gate = _first(tasks, r"ibl services disable IBL_DM\.RUN_AI_CALL")
        assert gate < _first(tasks, r"\bibl render\b")

    def test_a_connected_call_server_is_left_alone(self):
        """The LiveKit key is consulted before the service is turned off."""
        tasks = _tasks("single-server", "ibl_platform")
        script = _shell(tasks[_first(tasks, r"ibl services disable IBL_DM\.RUN_AI_CALL")])
        assert script.index("IBL_DM.LIVEKIT_API_KEY") < script.index("ibl services disable")


class TestSpaRestarts:
    @pytest.mark.parametrize("role", ["ibl_service_update", "ibl_launch_services"])
    def test_restarts_follow_the_enabled_spa_toggles(self, role):
        """Fresh servers run the preset's SPAs (auth, lms, os); restarting a fixed
        mentor/skills set started stray containers and left os and lms stale."""
        tasks = _tasks("single-server", role)
        discover = next(
            i for i, t in enumerate(tasks)
            if (t.get("ansible.builtin.import_role") or {}).get("tasks_from") == "discover"
        )
        restarts = [
            (i, t) for i, t in enumerate(tasks)
            if re.search(r"app/ibl-spa/.*docker compose up", _shell(t))
        ]
        looped = [i for i, t in restarts if t.get("loop") == "{{ enabled_spas }}"]
        assert looped and discover < min(looped)
        named = {n for _, t in restarts for n in re.findall(r"app/ibl-spa/([a-z]+)/", _shell(t))}
        assert named <= {"auth"}

    def test_microsoft_sso_restarts_no_spa_by_directory(self):
        """Its SPA restart goes through `ibl spa restart`, which skips SPAs whose
        toggle is off instead of starting them."""
        for task in _tasks("single-server", "microsoft_sso_config"):
            assert not re.search(r"app/ibl-spa/[a-z]+/.*docker compose", _shell(task))


class TestCallServer:
    def test_call_stack_starts_detached(self):
        """Attached `docker compose up` never returns, so the playbook hung."""
        ups = [_shell(t) for t in _tasks("call-server", "ibl_call") if re.search(r"\bibl call up\b", _shell(t))]
        assert ups and all(re.search(r"\bibl call up (-d|--detach)\b", s) for s in ups)


class TestStaticImports:
    @pytest.mark.parametrize(
        "tasks_file", sorted(TEMPLATES.glob("*/roles/*/tasks/*.yml")), ids=lambda p: str(p.relative_to(TEMPLATES))
    )
    def test_every_static_import_resolves(self, tasks_file):
        """A missing import target only fails when Ansible parses the playbook."""
        roles_dir = tasks_file.parents[2]
        for task in yaml.safe_load(tasks_file.read_text()) or []:
            role_import = task.get("ansible.builtin.import_role") or task.get("import_role")
            if role_import:
                target = roles_dir / role_import["name"] / "tasks" / f"{role_import.get('tasks_from', 'main')}.yml"
                assert target.is_file(), target
            file_import = task.get("ansible.builtin.import_tasks") or task.get("import_tasks")
            if file_import:
                assert (tasks_file.parent / file_import).is_file(), file_import


def _all_tasks():
    for tasks_file in sorted(TEMPLATES.glob("*/roles/*/tasks/*.yml")):
        for task in yaml.safe_load(tasks_file.read_text()) or []:
            yield tasks_file, task


def _imports(tasks: list[dict], target: str) -> int:
    """Index of the task that statically imports `target` (a tasks file or tasks_from)."""
    return next(
        i for i, t in enumerate(tasks)
        if t.get("ansible.builtin.import_tasks") == f"{target}.yml"
        or (t.get("ansible.builtin.import_role") or {}).get("tasks_from") == target
    )


class TestPlatformKeysAndStorage:
    def test_aws_keys_reach_secrets_before_the_first_render(self):
        """The rendered services read the AWS keys from secrets.yml; `aws configure`
        alone never reaches them."""
        tasks = _tasks("single-server", "ibl_platform")
        i = _first(tasks, r"--from-env AWS_ACCESS_KEY_ID=")
        task = tasks[i]
        assert i < _first(tasks, r"\bibl render\b")
        assert task.get("no_log") is True
        assert "aws_" not in _shell(task)  # values arrive through the task env
        assert any("aws_secret_access_key" in c for c in task["when"])

    def test_s3_storage_needs_both_buckets_and_precedes_the_render(self):
        tasks = _tasks("single-server", "ibl_platform")
        i = _first(tasks, r"ENABLE_S3_BUCKET_STORAGE=true")
        when = " ".join(tasks[i]["when"])
        assert "s3_static_bucket" in when and "s3_media_bucket" in when
        assert i < _first(tasks, r"\bibl render\b")

    def test_no_role_ever_turns_s3_storage_off(self):
        """Resetup and feature runs pass no buckets; a false write would silently
        move a live platform's uploads back to local disk."""
        for tasks_file, task in _all_tasks():
            assert not re.search(r"ENABLE_S3_BUCKET_STORAGE=(?i:false|0)", _shell(task)), tasks_file


class TestDmStartPrerequisites:
    def test_flowise_secrets_filled_after_generate_and_before_the_render(self):
        """`ibl dm up` requires them whenever AI is on; the preset never generates them."""
        tasks = _tasks("single-server", "ibl_platform")
        fill = _imports(tasks, "flowise_secrets")
        assert _first(tasks, r"ibl secrets generate") < fill < _first(tasks, r"\bibl render\b")

    def test_service_update_fills_them_before_the_dm_update(self):
        tasks = _tasks("single-server", "ibl_service_update")
        assert _imports(tasks, "flowise_secrets") < _first(tasks, r"\bibl dm update\b")

    def test_flowise_fill_leaves_other_secrets_alone(self):
        script = _shell(_tasks("single-server", "ibl_platform", "flowise_secrets.yml")[0])
        assert "ibl secrets rotate" in script and "--all-generated" not in script

    def test_meilisearch_resolvable_before_nginx_loads_config(self):
        """Host nginx resolves the meilisearch upstream at config load; `nginx -t` fails otherwise."""
        platform = _tasks("single-server", "ibl_platform")
        assert _imports(platform, "proxy_hosts") < _first(platform, r"ibl global-proxy launch")
        update = _tasks("single-server", "ibl_service_update")
        assert _imports(update, "proxy_hosts") < _first(update, r"ibl global-proxy reload")

    def test_hosts_entry_is_replaced_not_appended(self):
        task = next(t for t in _tasks("single-server", "ibl_platform", "proxy_hosts.yml") if "lineinfile" in t)
        assert task["lineinfile"]["regexp"]


class TestDmBootstrap:
    def test_no_role_runs_ibl_dm_launch(self):
        """It can't bootstrap a single server on cli-ops >= 7.13, and can't re-run."""
        for tasks_file, task in _all_tasks():
            assert not re.search(r"\bibl dm launch\b", _shell(task)), tasks_file

    def test_nothing_execs_into_web_before_the_dm_starts(self):
        """`docker compose exec web` fails until `ibl dm up -d` has started it."""
        tasks = _tasks("single-server", "ibl_dm")
        up = _first(tasks, r"\bibl dm up -d\b")
        assert _first(tasks, r"\bibl dm migrate\b") < up
        for task in tasks[:up]:
            assert not re.search(r"compose exec", _shell(task)), task["name"]

    def test_main_platform_exists_before_the_dm_starts(self):
        """With notifications on, the web container's start-up loads fixtures that
        reference the main platform; without it the container crash-loops."""
        tasks = _tasks("single-server", "ibl_dm")
        assert _first(tasks, r"\binitialize_manager\b") < _first(tasks, r"\bibl dm up -d\b")

    def test_dm_sso_runs_only_after_edx_is_launched(self):
        """`ibl dm sso` registers the DM's OIDC client in edX."""
        playbook = TEMPLATES / "single-server" / "playbook.yml"
        roles = [r if isinstance(r, str) else r["role"] for r in yaml.safe_load(playbook.read_text())[0]["roles"]]
        sso_roles = {f.parents[1].name for f, t in _all_tasks() if re.search(r"\bibl dm sso\b", _shell(t))}
        assert sso_roles == {"integrations"}
        assert roles.index("ibl_edx") < roles.index("integrations")


class TestClientRegistration:
    def test_no_role_uses_ibl_launch(self):
        """Its checked `docker network create` fails once the network exists."""
        for tasks_file, task in _all_tasks():
            assert not re.search(r"\bibl launch\b", _shell(task)), tasks_file

    def test_setup_and_service_update_share_the_client_step(self):
        assert _imports(_tasks("single-server", "integrations"), "clients") >= 0
        assert _imports(_tasks("single-server", "ibl_service_update"), "clients") >= 0


class TestServiceUpdateOnClientServers:
    def test_spa_sso_redirects_use_the_servers_domain(self):
        """service-update targets a host; its base_domain extra-var is a placeholder."""
        tasks = _tasks("single-server", "ibl_service_update")
        redirects = _first(tasks, r"REDIRECT_URIS")
        assert "{{ base_domain }}" not in _shell(tasks[redirects])
        assert _first(tasks, r"ibl config get BASE_DOMAIN") < redirects

    def test_fixed_password_test_users_are_opt_in(self):
        for task in _tasks("single-server", "ibl_service_update"):
            if "ibledu_2024" in _shell(task):
                assert "create_test_users" in str(task.get("when")), task["name"]

    def test_no_task_probes_a_fixed_spa_port(self):
        """Which SPAs run depends on the preset (fresh servers: auth/lms/os)."""
        for task in _tasks("single-server", "ibl_service_update"):
            assert not re.search(r"localhost:50\d\d/", _shell(task)), task["name"]


class TestInstallUrls:
    @pytest.mark.parametrize("kind", ["single-server", "call-server"])
    def test_token_never_in_install_urls(self, kind):
        """uv and git print the URL when an install fails."""
        for task in _tasks(kind, "ibl_cli_ops"):
            for line in _shell(task).splitlines():
                if "pip install" in line:
                    assert "git_access_token" not in line


class TestDmNotifications:
    def test_notifications_on_before_the_first_render(self):
        """The DM's other apps import its notifications app; with it off the DM
        can't migrate or start."""
        tasks = _tasks("single-server", "ibl_platform")
        assert _first(tasks, r"IBL_DM\.ENABLE_NOTIFICATIONS=true") < _first(tasks, r"\bibl render\b")

    def test_email_password_only_filled_when_empty(self):
        """With notifications on, render requires it; a real SMTP password must win."""
        tasks = _tasks("single-server", "ibl_platform")
        i = _first(tasks, r"--from-env IBL_DM\.EMAIL_HOST_PASSWORD=")
        script = _shell(tasks[i])
        assert script.index("ibl config get IBL_DM.EMAIL_HOST_PASSWORD") < script.index("ibl secrets set")
        assert i < _first(tasks, r"\bibl render\b")

    def test_smtp_writes_the_dm_email_password_too(self):
        """Otherwise the setup placeholder would keep the DM's mail from authenticating."""
        tasks = _tasks("single-server", "smtp_config")
        script = _shell(tasks[_first(tasks, r"--from-env IBL_SMTP_PASSWORD=")])
        assert "--from-env IBL_DM.EMAIL_HOST_PASSWORD=" in script
