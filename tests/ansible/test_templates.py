"""Ordering and selection rules the playbook templates depend on.

These read the role YAML: each rule, broken, only shows up on a server,
usually as the first `ibl render` failing mid-bootstrap.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ansible.parsing.splitter import split_args

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


class TestCredentialRegistration:
    def test_no_role_runs_the_create_only_helpers(self):
        """They fail or skip once their rows exist: a re-run failed on a duplicate
        client, and a resetup's rotated clients never reached the DM or edX."""
        for tasks_file, task in _all_tasks():
            assert not re.search(
                r"\bibl dm auth-setup\b|manager_credential_setup|EdxManagerConfigTaskRunner|add_new_credentials",
                _shell(task),
            ), tasks_file

    @pytest.mark.parametrize("role", ["ibl_dm", "ibl_service_update"])
    def test_setup_and_service_update_share_the_dm_step(self, role):
        tasks = _tasks("single-server", role)
        assert _first(tasks, r"\bmigrate\b") < _imports(tasks, "credentials")

    def test_dm_step_follows_the_admin_user(self):
        """The DM's application for edX belongs to that user."""
        tasks = _tasks("single-server", "ibl_dm")
        assert _first(tasks, r"create_superuser") < _imports(tasks, "credentials")

    @pytest.mark.parametrize("file", ["ibl_dm/tasks/credentials.yml", "integrations/tasks/clients.yml"])
    def test_client_secrets_stay_off_command_lines_and_logs(self, file):
        """The upstream helpers put them in the command, which a failure printed."""
        tasks = yaml.safe_load((TEMPLATES / "single-server/roles" / file).read_text())
        payloads = [t for t in tasks if re.search(r"docker (compose run|exec) .*-e \w+", _shell(t))]
        assert payloads
        for task in payloads:
            assert task.get("no_log") is True, task["name"]
            assert not re.search(r"-e \w+=", _shell(task)), task["name"]


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


class TestDataSeeding:
    def test_mentor_settings_follow_the_mentor_seed(self):
        """Chat reads the settings row; `seed_flows` creates mentors without one."""
        tasks = _tasks("single-server", "data_seeding")
        assert _first(tasks, r"\bseed_flows\b") < _first(tasks, r"get_or_create_mentor_settings")


_REDIRECT_KEY = "IBL_EDX.IBL_EDX_REDIRECTOR.IBL_REDIRECTOR_EXTERNAL_ROOT_URL"
_FAKE_IBL = """#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_IBL_STATE"]
state = json.load(open(path))
args = sys.argv[1:]
if args[:3] == ["services", "list", "--json"]:
    print(json.dumps({k: {"value": True} for k in state["enabled"]}))
elif args[:2] == ["config", "get"]:
    value, source = state["config"][args[2]]
    print(json.dumps({"key": args[2], "value": value, "source": source}))
elif args[:2] == ["config", "set"]:
    key, value = args[2].split("=", 1)
    state["config"][key] = [value, "config"]
    state["sets"].append(args[2])
    json.dump(state, open(path, "w"))
elif args == ["render"]:
    pass
else:
    sys.exit("unexpected: " + " ".join(args))
"""


class TestLmsRootRedirect:
    TASK = "Send the LMS root to the LMS SPA when the skills SPA is off"

    def _script(self) -> str:
        task = next(t for t in _tasks("single-server", "ibl_platform") if t["name"] == self.TASK)
        return re.search(r"<<'PY'\n(.*?)\nPY\n", _shell(task), re.S).group(1)

    def _run(self, tmp_path, enabled, value, source):
        (tmp_path / "ibl").write_text(_FAKE_IBL)
        (tmp_path / "ibl").chmod(0o755)
        state = tmp_path / "state.json"
        config = {"BASE_DOMAIN": ["new.example.com", "config"], _REDIRECT_KEY: [value, source]}
        state.write_text(json.dumps({"enabled": enabled, "config": config, "sets": []}))
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "FAKE_IBL_STATE": str(state)}
        subprocess.run([sys.executable, "-"], input=self._script(), text=True, env=env, check=True)
        return json.loads(state.read_text())["sets"]

    def test_runs_before_the_first_render(self):
        """edX renders its settings from config; a later write waits for the next render."""
        tasks = _tasks("single-server", "ibl_platform")
        assert _first(tasks, r"IBL_REDIRECTOR_EXTERNAL_ROOT_URL") < _first(tasks, r"\bibl render\b")

    @pytest.mark.parametrize(
        "value, source",
        [("https://skills.new.example.com", "default"), ("https://lms.old.example.com", "config")],
        ids=["preset-default", "previous-domain"],
    )
    def test_points_the_root_at_the_lms_spa(self, tmp_path, value, source):
        """The preset runs the LMS SPA and not skills, whose host doesn't exist."""
        enabled = ["IBL_SPA.RUN_AUTH_SPA", "IBL_SPA.RUN_LMS_SPA", "IBL_SPA.RUN_OS_SPA"]
        assert self._run(tmp_path, enabled, value, source) == [f"{_REDIRECT_KEY}=https://lms.new.example.com"]

    @pytest.mark.parametrize(
        "enabled, value, source",
        [
            (["IBL_SPA.RUN_LMS_SPA"], "https://lms.new.example.com", "config"),
            (["IBL_SPA.RUN_LMS_SPA"], "https://landing.example.org/", "config"),
            (["IBL_SPA.RUN_LMS_SPA", "IBL_SPA.RUN_SKILLS_SPA"], "https://skills.new.example.com", "default"),
            (["IBL_SPA.RUN_AUTH_SPA"], "https://skills.new.example.com", "default"),
        ],
        ids=["already-set", "operator-value", "skills-spa-on", "no-lms-spa"],
    )
    def test_leaves_the_root_alone(self, tmp_path, enabled, value, source):
        assert self._run(tmp_path, enabled, value, source) == []


class TestTenantDisplayName:
    """`platform create` reuses the tenant role; writing the display name every
    time renamed the whole deployment after each tenant added."""

    def _run(self, tmp_path, source):
        task = next(t for t in _tasks("single-server", "ibl_tenant_platform") if t["name"].startswith("Set PLATFORM_NAME"))
        script = _shell(task).split("set -o pipefail", 1)[1].replace("{{ platform_name | upper }}", "ACME")
        (tmp_path / "ibl").write_text(_FAKE_IBL)
        (tmp_path / "ibl").chmod(0o755)
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"enabled": [], "config": {"PLATFORM_NAME": ["EXAMPLE", source]}, "sets": []}))
        env = {"PATH": f"{tmp_path}:{Path(sys.executable).parent}:/usr/bin:/bin", "FAKE_IBL_STATE": str(state)}
        subprocess.run(["bash", "-c", "set -o pipefail" + script], env=env, check=True)
        return json.loads(state.read_text())["sets"]

    def test_the_setup_tenant_names_the_deployment(self, tmp_path):
        assert self._run(tmp_path, "default") == ["PLATFORM_NAME=ACME"]

    def test_a_later_tenant_leaves_it(self, tmp_path):
        assert self._run(tmp_path, "config") == []


class TestSecretRotation:
    def test_no_role_rotates_every_generated_secret(self):
        """`--all-generated` also rotates the keys that encrypt stored data; after
        a resetup the DM could no longer read its own credential rows."""
        for tasks_file, task in _all_tasks():
            assert "--all-generated" not in _shell(task), tasks_file

    def test_the_dm_field_encryption_key_is_kept(self):
        script = _shell(_tasks("single-server", "ibl_platform", "rotate_secrets.yml")[0])
        keep = script[script.index("KEEP = {"):script.index("}", script.index("KEEP = {"))]
        assert '"IBL_DM.FIELD_ENCRYPTION_KEY"' in keep

    @pytest.mark.parametrize("role", ["ibl_platform", "ibl_launch"])
    def test_resetup_and_launch_share_the_rotation(self, role):
        assert _imports(_tasks("single-server", role), "rotate_secrets") >= 0


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


class TestAnsibleLoadsEveryTask:
    @pytest.mark.parametrize(
        "tasks_file", sorted(TEMPLATES.glob("*/roles/*/tasks/*.yml")), ids=lambda p: str(p.relative_to(TEMPLATES))
    )
    def test_free_form_commands_split(self, tasks_file):
        """Ansible splits a free-form command on its quotes when it loads the
        playbook; one apostrophe in a script comment failed the whole run."""
        for task in _walk(yaml.safe_load(tasks_file.read_text())):
            for module in ("shell", "ansible.builtin.shell", "command", "ansible.builtin.command"):
                if isinstance(task.get(module), str):
                    split_args(task[module])


class TestTenantsKeepTheMainKeys:
    """The DM's `seed_flows` gives every platform without one a "use main LLM
    key" setting that defaults to off, so each resetup cut the existing tenants
    off from the deployment's keys."""

    def _seed(self, settings: dict[str, bool], platforms: list[str]) -> dict[str, bool]:
        """Run the seed task's script with `seed_flows` doing what the DM does."""
        import types
        from unittest.mock import patch

        rows = {k: types.SimpleNamespace(platform_id=k, use_main_key=v) for k, v in settings.items()}

        class Rows(list):
            def values_list(self, field, flat):
                return [getattr(r, field) for r in self]

            def filter(self, platform_id__in, use_main_key):
                return Rows(r for r in self if r.platform_id in platform_id__in and r.use_main_key == use_main_key)

            def update(self, use_main_key):
                for r in self:
                    r.use_main_key = use_main_key
                return len(self)

        use_main_key = types.SimpleNamespace(objects=types.SimpleNamespace(
            values_list=lambda *a, **k: Rows(rows.values()).values_list(*a, **k),
            filter=lambda **k: Rows(rows.values()).filter(**k),
        ))
        platform = types.SimpleNamespace(objects=types.SimpleNamespace(
            all=lambda: [types.SimpleNamespace(id=k, key=k) for k in platforms],
        ))

        def seed_flows(name):
            for key in platforms:
                rows.setdefault(key, types.SimpleNamespace(platform_id=key, use_main_key=False))

        modules = {name: types.ModuleType(name) for name in (
            "core", "core.models", "django", "django.core", "django.core.management",
            "ibl_ai_mentor", "ibl_ai_mentor.models",
        )}
        modules["core.models"].Platform = platform
        modules["django.core.management"].call_command = seed_flows
        modules["ibl_ai_mentor.models"].UseMainLLMKey = use_main_key
        task = next(t for t in _tasks("single-server", "data_seeding") if t["name"] == "Seed DM flows")
        code = re.search(r"<<'PY'\n(.*?)\nPY\n", _shell(task), re.S).group(1)
        with patch.dict(sys.modules, modules):
            exec(code, {})
        return {k: r.use_main_key for k, r in rows.items()}

    def test_tenants_without_a_setting_keep_the_main_keys(self):
        assert self._seed({"main": True}, ["main", "acme", "globex"]) == {"main": True, "acme": True, "globex": True}

    def test_an_admin_setting_is_kept(self):
        assert self._seed({"main": True, "acme": False, "globex": True}, ["main", "acme", "globex"]) == {
            "main": True, "acme": False, "globex": True,
        }

    def test_a_new_tenant_is_given_the_main_keys(self):
        """After its launch, and only when this run created it."""
        tasks = _tasks("single-server", "ibl_tenant_platform")
        names = [t["name"] for t in tasks]
        step = tasks[names.index("Let the new tenant use the deployment's LLM keys")]
        assert names.index("Launch tenant platform via run_launch_steps") < names.index(step["name"])
        assert "'use_main_key': True" in _shell(step)
        assert any("TENANT_PLATFORM_STATUS:ABSENT" in c for c in step["when"])


class TestFreshInstallAccess:
    def test_health_mentors_skip_moderation_once_seeded(self):
        """Their moderation builds a real model; with only a gateway key the
        health check, and the readiness gate of `ibl dm update`, failed."""
        tasks = _tasks("single-server", "data_seeding")
        names = [t["name"] for t in tasks]
        i = names.index("Keep the health-check mentors off the moderation model")
        assert _first(tasks, r"\bseed_flows\b") < i
        assert "llm_provider='fake-llm'" in _shell(tasks[i])

    def test_the_super_admin_administers_main(self):
        """The platform reads admin rights from the platform link, not the superuser flag.

        The link points at the DM's copy of the LMS user, which exists only once
        the LMS user is synced; on a fresh install the lookup failed before that.
        """
        tasks = _tasks("single-server", "admin_setup")
        names = [t["name"] for t in tasks]
        i = names.index("Make the super admin an admin of the main platform")
        lms_admin = names.index("Create LMS super admin (with first_name / last_name / UserProfile.name)")
        assert lms_admin < _first(tasks, r"ibl edx sync-with-manager --users") < i
        script = _shell(tasks[i])
        assert "'is_admin': True" in script and "key='main'" in script and "{{ admin_username }}" in script
