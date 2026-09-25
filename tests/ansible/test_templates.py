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
