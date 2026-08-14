"""Tests for the remaining post-setup features and the `configure` menu."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from pydantic import ValidationError

from iblai_infra.features.llm import LLM_TAGS, llm_set_key
from iblai_infra.features.platform import PLATFORM_TAGS, platform_create
from iblai_infra.features.sso import GOOGLE_TAGS, MICROSOFT_TAGS, sso_google, sso_microsoft
from iblai_infra.features.stripe import STRIPE_TAGS, stripe_enable
from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DNSConfig,
    Environment,
    InfraConfig,
    LLMProvider,
    NetworkConfig,
    ProjectState,
    SSHConfig,
    SSHKeyMethod,
    SetupConfig,
)


def _state(**kwargs) -> ProjectState:
    cfg = InfraConfig(
        project_name="acme",
        environment=Environment.PROD,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY, region="us-east-1",
            access_key_id="AK", secret_access_key="SK",
        ),
        network=NetworkConfig(vpn_ip="1.2.3.4"),
        compute=ComputeConfig(),
        ssh=SSHConfig(
            method=SSHKeyMethod.GENERATE, key_name="k",
            private_key_path=Path("/tmp/key.pem"),
        ),
        certificates=CertificateConfig(method=CertMethod.NONE),
        dns=DNSConfig(base_domain="acme.example.com"),
    )
    defaults = dict(
        name="acme", config=cfg, outputs={"instance_public_ip": "192.0.2.10"},
        workspace_path="/tmp/ws", status="created", setup_status="completed",
    )
    defaults.update(kwargs)
    return ProjectState(**defaults)


class _Captured:
    """Records what run_feature was asked to apply."""

    def __init__(self):
        self.tags = None
        self.config = None

    def __call__(self, state, config, *, tags, labels, name, what, action="configured",
                 live_now=True, not_live_hint=""):
        self.tags = tags
        self.config = config


@pytest.fixture
def applied(monkeypatch):
    """Patch run_feature in every feature module and capture the call."""
    cap = _Captured()
    for mod in ("llm", "platform", "sso", "stripe"):
        monkeypatch.setattr(f"iblai_infra.features.{mod}.run_feature", cap)
    monkeypatch.setattr(
        "iblai_infra.terraform.state.load_state", lambda name: _state()
    )
    return cap


# ---------------------------------------------------------------------------
# Tag routing — each command must run only its own role
# ---------------------------------------------------------------------------


class TestTagRouting:
    def test_each_feature_targets_exactly_one_role(self):
        assert GOOGLE_TAGS == ["google_sso"]
        assert MICROSOFT_TAGS == ["microsoft_sso"]
        assert STRIPE_TAGS == ["stripe"]
        assert LLM_TAGS == ["llm"]
        assert PLATFORM_TAGS == ["platform"]

    def test_no_two_features_share_a_tag(self):
        all_tags = GOOGLE_TAGS + MICROSOFT_TAGS + STRIPE_TAGS + LLM_TAGS + PLATFORM_TAGS
        assert len(all_tags) == len(set(all_tags))


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class TestLLM:
    def test_api_key_flag_skips_the_prompt(self, applied):
        with patch("questionary.password") as q:
            llm_set_key(name="acme", api_key="test-key-value", provider=None)
            q.assert_not_called()
        assert applied.tags == LLM_TAGS
        assert applied.config.llm_api_key == "test-key-value"

    def test_empty_key_is_rejected(self, applied):
        with pytest.raises(typer.Exit):
            llm_set_key(name="acme", api_key="   ", provider=None)

    def test_key_is_not_persisted_to_state(self, applied):
        llm_set_key(name="acme", api_key="test-key-value", provider=None)
        assert "test-key-value" not in applied.config.model_dump_json()

    def test_defaults_to_openai_when_non_interactive(self, applied):
        llm_set_key(name="acme", api_key="k", provider=None)
        assert applied.config.llm_provider == LLMProvider.OPENAI

    @pytest.mark.parametrize("given", ["anthropic", "Anthropic", "  ANTHROPIC  "])
    def test_provider_is_normalised_to_lowercase(self, applied, given):
        """The name is matched exactly on the server, so case must not survive."""
        llm_set_key(name="acme", api_key="k", provider=given)
        assert applied.config.llm_provider == LLMProvider.ANTHROPIC
        assert applied.config.llm_provider.value == "anthropic"

    @pytest.mark.parametrize("bad", ["gemini", "openai-2", "", "azure openai"])
    def test_unknown_providers_are_rejected(self, applied, bad):
        """A row named anything else is written and then never read."""
        with pytest.raises(typer.Exit):
            llm_set_key(name="acme", api_key="k", provider=bad)
        assert applied.tags is None

    def test_provider_reaches_ansible_as_a_plain_string(self):
        """The role interpolates this into the credential name."""
        from iblai_infra.ansible.runner import AnsibleRunner

        state = _state()
        config = SetupConfig.for_feature(
            state, llm_provider=LLMProvider.ANTHROPIC, llm_api_key="k"
        )
        built = AnsibleRunner(state, config)._build_extra_vars()
        assert built["llm_provider"] == "anthropic"
        assert built["llm_api_key"] == "k"


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


class TestPlatform:
    def test_creates_a_named_tenant(self, applied):
        platform_create(name="acme", platform_name="research")
        assert applied.tags == PLATFORM_TAGS
        assert applied.config.platform_name == "research"

    def test_name_is_lowercased(self, applied):
        platform_create(name="acme", platform_name="Research")
        assert applied.config.platform_name == "research"

    def test_main_is_rejected(self, applied):
        """`main` always exists; the role no-ops on it, so fail early instead."""
        with pytest.raises(typer.Exit):
            platform_create(name="acme", platform_name="main")

    @pytest.mark.parametrize("bad", ["has space", "UPPER CASE", "-leading", "semi;colon", ""])
    def test_invalid_names_are_rejected(self, applied, bad):
        with pytest.raises(typer.Exit):
            platform_create(name="acme", platform_name=bad)

    @pytest.mark.parametrize("good", ["research", "team-a", "team_b", "x1"])
    def test_valid_names_are_accepted(self, applied, good):
        platform_create(name="acme", platform_name=good)
        assert applied.config.platform_name == good


# ---------------------------------------------------------------------------
# SSO
# ---------------------------------------------------------------------------


class TestGoogleSSO:
    def test_collects_credentials_and_enables(self, applied):
        with patch("iblai_infra.features.sso.prompt_required", side_effect=["cid", "secret"]), \
             patch("iblai_infra.features.sso.prompt_optional", return_value="example.com"):
            sso_google(name="acme")
        assert applied.tags == GOOGLE_TAGS
        assert applied.config.google_sso_enabled is True
        assert applied.config.google_sso_client_id == "cid"
        assert applied.config.google_sso_organization == "example.com"

    def test_does_not_request_a_restart(self, applied):
        """The provider row is read per request, so nothing needs recreating."""
        with patch("iblai_infra.features.sso.prompt_required", side_effect=["cid", "secret"]), \
             patch("iblai_infra.features.sso.prompt_optional", return_value=""):
            sso_google(name="acme")
        assert applied.config.restart_services is False

    def test_secret_is_not_persisted_to_state(self, applied):
        with patch("iblai_infra.features.sso.prompt_required", side_effect=["cid", "top-secret"]), \
             patch("iblai_infra.features.sso.prompt_optional", return_value=""):
            sso_google(name="acme")
        assert "top-secret" not in applied.config.model_dump_json()


class TestMicrosoftSSO:
    def test_collects_credentials_and_enables(self, applied):
        with patch("iblai_infra.features.sso.prompt_required", side_effect=["cid", "secret", "tid"]), \
             patch("iblai_infra.features.sso.prompt_optional", return_value="Acme"), \
             patch("questionary.confirm") as q:
            q.return_value.ask.return_value = True
            sso_microsoft(name="acme", assume_yes=False)
        assert applied.tags == MICROSOFT_TAGS
        assert applied.config.microsoft_sso_enabled is True
        assert applied.config.microsoft_sso_tenant_id == "tid"

    def test_warns_about_the_edx_restart_and_can_be_declined(self, applied):
        """Its role restarts edX; the operator should get a say before that."""
        with patch("iblai_infra.features.sso.prompt_required", side_effect=["cid", "secret", "tid"]), \
             patch("iblai_infra.features.sso.prompt_optional", return_value=""), \
             patch("questionary.confirm") as q:
            q.return_value.ask.return_value = False
            # ui.abort() raises SystemExit rather than typer.Exit.
            with pytest.raises((SystemExit, typer.Exit)):
                sso_microsoft(name="acme", assume_yes=False)
        assert applied.tags is None  # nothing applied

    def test_assume_yes_skips_the_warning(self, applied):
        with patch("iblai_infra.features.sso.prompt_required", side_effect=["cid", "secret", "tid"]), \
             patch("iblai_infra.features.sso.prompt_optional", return_value=""), \
             patch("questionary.confirm") as q:
            sso_microsoft(name="acme", assume_yes=True)
            q.assert_not_called()
        assert applied.tags == MICROSOFT_TAGS


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


class TestStripe:
    def _run(self, mode="test"):
        return (
            patch("questionary.select"),
            patch("iblai_infra.features.stripe.prompt_required", side_effect=["sk_x", "pk_x"]),
            patch("iblai_infra.features.stripe.prompt_optional", return_value=""),
        )

    def test_enables_in_test_mode(self, applied):
        sel, req, opt = self._run()
        with sel as s, req, opt:
            s.return_value.ask.return_value = "test"
            stripe_enable(name="acme")
        assert applied.tags == STRIPE_TAGS
        assert applied.config.stripe_enabled is True
        assert applied.config.stripe_mode == "test"

    def test_live_mode_is_carried_through(self, applied):
        sel, req, opt = self._run()
        with sel as s, req, opt:
            s.return_value.ask.return_value = "live"
            stripe_enable(name="acme")
        assert applied.config.stripe_mode == "live"

    def test_secret_key_is_not_persisted_to_state(self, applied):
        sel, req, opt = self._run()
        with sel as s, req, opt:
            s.return_value.ask.return_value = "test"
            stripe_enable(name="acme")
        assert "sk_x" not in applied.config.model_dump_json()

    def test_does_not_request_a_restart(self, applied):
        sel, req, opt = self._run()
        with sel as s, req, opt:
            s.return_value.ask.return_value = "test"
            stripe_enable(name="acme")
        assert applied.config.restart_services is False


# ---------------------------------------------------------------------------
# configure menu
# ---------------------------------------------------------------------------


class TestConfigureMenu:
    @pytest.mark.parametrize(
        "choice,target",
        [
            ("smtp", "iblai_infra.features.smtp.smtp_enable"),
            ("google", "iblai_infra.features.sso.sso_google"),
            ("microsoft", "iblai_infra.features.sso.sso_microsoft"),
            ("stripe", "iblai_infra.features.stripe.stripe_enable"),
            ("llm", "iblai_infra.features.llm.llm_set_key"),
            ("platform", "iblai_infra.features.platform.platform_create"),
        ],
    )
    def test_each_choice_dispatches_to_the_same_command(self, choice, target, monkeypatch):
        """The menu must not reimplement anything the direct form does."""
        from iblai_infra.features.configure import configure

        monkeypatch.setattr("iblai_infra.terraform.state.load_state", lambda name: _state())
        with patch("questionary.select") as sel, patch(target) as fn:
            sel.return_value.ask.return_value = choice
            configure(name="acme")
            fn.assert_called_once()

    def test_cancel_does_nothing(self, monkeypatch):
        from iblai_infra.features.configure import configure

        monkeypatch.setattr("iblai_infra.terraform.state.load_state", lambda name: _state())
        with patch("questionary.select") as sel, \
             patch("iblai_infra.features.smtp.smtp_enable") as smtp:
            sel.return_value.ask.return_value = None
            configure(name="acme")
            smtp.assert_not_called()

    def test_invalid_environment_fails_before_the_menu(self, monkeypatch):
        from iblai_infra.features.configure import configure

        monkeypatch.setattr("iblai_infra.terraform.state.load_state", lambda name: None)
        with patch("questionary.select") as sel:
            with pytest.raises(typer.Exit):
                configure(name="nope")
            sel.assert_not_called()


# ---------------------------------------------------------------------------
# Topology guard — call servers carry none of these roles
# ---------------------------------------------------------------------------


class TestCallServerGuard:
    def _call_state(self):
        from iblai_infra.models import DeploymentType

        st = _state()
        st.config.deployment_type = DeploymentType.CALL
        return st

    def test_call_server_is_refused(self, monkeypatch):
        """Otherwise the tagged run matches zero tasks, exits 0, and lies."""
        from iblai_infra.features._common import load_feature_target

        monkeypatch.setattr(
            "iblai_infra.terraform.state.load_state", lambda name: self._call_state()
        )
        with pytest.raises(typer.Exit):
            load_feature_target("acme")

    def test_single_server_is_allowed(self, monkeypatch):
        from iblai_infra.features._common import load_feature_target

        monkeypatch.setattr("iblai_infra.terraform.state.load_state", lambda name: _state())
        assert load_feature_target("acme") is not None


class TestNoOpRunIsNotSuccess:
    def _runner(self):
        from iblai_infra.ansible.runner import AnsibleRunner
        from iblai_infra.models import SetupConfig

        return AnsibleRunner(_state(), SetupConfig.for_feature(_state()))

    def test_zero_tasks_is_reported_as_failure(self):
        """ansible exits 0 when a tag matches nothing; that is not success."""
        runner = self._runner()
        with patch.object(runner, "_run_ansible", return_value=(True, 0)), \
             patch.object(runner, "_print_final_table"):
            assert runner.run_partial(["smtp"]) is False

    def test_work_done_is_still_success(self):
        runner = self._runner()
        with patch.object(runner, "_run_ansible", return_value=(True, 1)), \
             patch.object(runner, "_print_final_table"):
            assert runner.run_partial(["smtp"]) is True


# ---------------------------------------------------------------------------
# The credential row the LLM task writes
#
# These assert on the role's script text rather than a live server. The shape
# is a contract with the platform's data model, and getting it wrong fails
# silently - the run reports success and the key is simply never used.
# ---------------------------------------------------------------------------


class TestLLMCredentialRow:
    @staticmethod
    def _task():
        import yaml

        tasks = yaml.safe_load(
            (
                Path(__file__).resolve().parents[2]
                / "src/iblai_infra/ansible/templates/single-server"
                / "roles/admin_setup/tasks/main.yml"
            ).read_text()
        )
        # two tasks carry the tag now - the guard and the write itself
        return next(t for t in tasks if "llm" in (t.get("tags") or []) and "shell" in t)

    def test_writes_both_the_plaintext_and_encrypted_columns(self):
        """Newer platforms read the encrypted column and never fall back."""
        script = self._task()["shell"]
        assert "'value', 'value_encrypted'" in script

    def test_payload_is_the_same_key_object_for_both(self):
        script = self._task()["shell"]
        assert "value = {'key': '{{ llm_api_key }}'}" in script
        # one object assigned to whichever columns exist - they cannot diverge
        assert "defaults[column] = value" in script

    def test_columns_are_probed_not_assumed(self):
        """`value_encrypted` is absent on older deployments."""
        script = self._task()["shell"]
        assert "_meta.get_fields()" in script

    def test_name_comes_from_the_provider_verbatim(self):
        script = self._task()["shell"]
        assert "name='{{ llm_provider }}'" in script

    def test_other_providers_lose_preferred(self):
        """The server takes the first preferred row with no tie-break."""
        script = self._task()["shell"]
        assert "exclude(name='{{ llm_provider }}').update(is_preferred=False)" in script

    def test_the_key_is_not_echoed_on_failure(self):
        assert self._task()["no_log"] is True

    def test_task_is_skipped_without_a_key(self):
        assert self._task()["when"] == "llm_api_key | length > 0"


class TestLLMKeyIsNotCode:
    """The key lands inside a Python program inside a shell string.

    A quote ends the Python literal, a double quote ends the shell string, and
    `$(...)` is substituted by the shell before the command runs. In CI the key
    comes from a secret store, which is not necessarily the same trust boundary
    as the operator running the command.
    """

    HOSTILE = [
        "$(touch /tmp/pwned)",
        "`touch /tmp/pwned`",
        'x"; touch /tmp/pwned; echo "',
        "x'); import os; os.system('id'); ('",
        "key with spaces",
        "key\nnewline",
        "${IFS}",
    ]

    @pytest.mark.parametrize("hostile", HOSTILE)
    def test_the_model_rejects_them(self, hostile):
        with pytest.raises(ValidationError):
            SetupConfig(
                ssh_private_key_path=Path("/tmp/k.pem"),
                target_host="192.0.2.10",
                base_domain="acme.example.com",
                llm_api_key=hostile,
            )

    @pytest.mark.parametrize("hostile", HOSTILE)
    def test_the_command_rejects_them(self, applied, hostile):
        with pytest.raises(typer.Exit):
            llm_set_key(name="acme", api_key=hostile, provider=None)
        assert applied.tags is None

    @pytest.mark.parametrize(
        "realistic",
        [
            "sk-proj-EXAMPLE_not-a-real-key",
            "sk-ant-EXAMPLE_not-a-real-key",
            "abc.def.ghi",
            "A1",
        ],
    )
    def test_real_provider_keys_still_pass(self, applied, realistic):
        llm_set_key(name="acme", api_key=realistic, provider=None)
        assert applied.config.llm_api_key == realistic

    def test_the_role_checks_independently_of_the_cli(self):
        """A role that builds a shell command must not trust its caller."""
        import yaml

        tasks = yaml.safe_load(
            (
                Path(__file__).resolve().parents[2]
                / "src/iblai_infra/ansible/templates/single-server"
                / "roles/admin_setup/tasks/main.yml"
            ).read_text()
        )
        guards = [t for t in tasks if "ansible.builtin.assert" in t]
        assert guards, "no guard assertion on the LLM task"
        assertions = " ".join(guards[0]["ansible.builtin.assert"]["that"])
        assert "llm_api_key is match" in assertions
        assert "llm_provider in" in assertions
