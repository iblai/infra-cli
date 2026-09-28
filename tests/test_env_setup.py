"""Tests for iblai_infra.env_setup.build_setup_config_from_env and
build_bootstrap_state_from_env.

Covers the .env → SetupConfig pipeline used by `iblai infra setup-env`.
Network-free — `validate_key_permissions` is patched so chmod/stat
side effects don't escape tmp_path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from tests.conftest import CLI_OPS_TEST_TAG

from iblai_infra.env_setup import (
    build_bootstrap_state_from_env,
    build_setup_config_from_env,
)
from iblai_infra.models import (
    DeploymentType,
    LLMProvider,
    ProjectState,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

def _required_env(**overrides) -> dict[str, str]:
    """Smallest .env that satisfies always-required keys."""
    base = {
        "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "GIT_TOKEN": "test-pat-value",
        "ADMIN_USERNAME": "platform_admin",
        "ADMIN_EMAIL": "admin@example.com",
        "ADMIN_PASSWORD": "change-me-min-8-chars",
    }
    base.update(overrides)
    return base


def _freestanding_env(tmp_key: Path, **overrides) -> dict[str, str]:
    """Required + free-standing fields; SSH key path defaults to tmp_key."""
    base = _required_env()
    base.update({
        "PROJECT_NAME": "freestand",
        "TARGET_HOST": "203.0.113.42",
        "SSH_PRIVATE_KEY_PATH": str(tmp_key),
        "BASE_DOMAIN": "free.example.com",
    })
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _no_chmod(monkeypatch):
    """validate_key_permissions does a chmod under the hood. In tests we
    only care about its truthiness; keep it pure."""
    monkeypatch.setattr(
        "iblai_infra.env_setup.validate_key_permissions",
        lambda path: True,
    )
    yield


@pytest.fixture(autouse=True)
def _pin_resolver():
    """CLI_OPS_RELEASE_TAG unset → env_setup resolves the tag from the
    prod-images pin over the network. Stub it for determinism/offline."""
    with patch(
        "iblai_infra.env_setup.resolve_pinned_cli_ops_tag",
        return_value=CLI_OPS_TEST_TAG,
    ) as m:
        yield m


@pytest.fixture(autouse=True)
def _no_state_persistence(monkeypatch):
    """build_bootstrap_state_from_env writes state.json. Stub it."""
    monkeypatch.setattr("iblai_infra.env_setup.save_state", lambda state: None)
    yield


@pytest.fixture
def ssh_key(tmp_path):
    p = tmp_path / "id_ed25519"
    p.write_text("fake-private-key")
    p.chmod(0o600)
    return p


@pytest.fixture
def project_state(project_state, ssh_key):
    """Override conftest's project_state — its default ssh_key_path
    (/tmp/testkey.pem) doesn't actually exist, so env_setup's
    file-existence check would reject it. Point at a real tmp file."""
    project_state.config.ssh.private_key_path = ssh_key
    return project_state


# ---------------------------------------------------------------------------
# build_setup_config_from_env — provisioned-name mode
# ---------------------------------------------------------------------------

class TestProvisionedMode:
    def test_minimal_env_produces_valid_config(self, project_state):
        config = build_setup_config_from_env(_required_env(), state=project_state)
        assert config.target_host == "54.123.45.67"  # from project_state fixture
        assert config.base_domain == "example.com"
        assert config.aws_default_region == "us-east-1"
        assert config.admin_username == "platform_admin"

    def test_aws_default_region_derived_from_state(self, project_state):
        project_state.config.credentials.region = "eu-west-1"
        config = build_setup_config_from_env(_required_env(), state=project_state)
        assert config.aws_default_region == "eu-west-1"

    def test_env_overrides_target_host(self, project_state):
        env = _required_env(TARGET_HOST="198.51.100.99")
        config = build_setup_config_from_env(env, state=project_state)
        assert config.target_host == "198.51.100.99"

    def test_multi_server_state_rejected(self, project_state):
        project_state.config.deployment_type = DeploymentType.MULTI
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(_required_env(), state=project_state)

    def test_call_server_state_rejected(self, project_state):
        project_state.config.deployment_type = DeploymentType.CALL
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(_required_env(), state=project_state)

    def test_no_instance_ip_in_state_errors(self, project_state):
        project_state.outputs = {}
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(_required_env(), state=project_state)


# ---------------------------------------------------------------------------
# Required-key validation
# ---------------------------------------------------------------------------

class TestRequiredKeys:
    def test_missing_aws_keys(self, project_state):
        env = _required_env(AWS_ACCESS_KEY_ID="", AWS_SECRET_ACCESS_KEY="")
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)

    def test_missing_git_token(self, project_state):
        env = _required_env()
        del env["GIT_TOKEN"]
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)

    def test_git_access_token_alias_accepted(self, project_state):
        env = _required_env()
        del env["GIT_TOKEN"]
        env["GIT_ACCESS_TOKEN"] = "alias-pat-value"
        config = build_setup_config_from_env(env, state=project_state)
        assert config.git_access_token == "alias-pat-value"

    def test_missing_admin_email(self, project_state):
        env = _required_env()
        del env["ADMIN_EMAIL"]
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)

    def test_invalid_admin_email_no_at(self, project_state):
        env = _required_env(ADMIN_EMAIL="not-an-email")
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)

    def test_admin_password_too_short(self, project_state):
        env = _required_env(ADMIN_PASSWORD="short")
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)


# ---------------------------------------------------------------------------
# SSH key resolution
# ---------------------------------------------------------------------------

class TestSshKey:
    def test_ssh_path_derived_from_state(self, project_state, ssh_key):
        project_state.config.ssh.private_key_path = ssh_key
        config = build_setup_config_from_env(_required_env(), state=project_state)
        assert config.ssh_private_key_path == ssh_key

    def test_ssh_path_overridden_by_env(self, project_state, tmp_path):
        override = tmp_path / "other.pem"
        override.write_text("k")
        env = _required_env(SSH_PRIVATE_KEY_PATH=str(override))
        config = build_setup_config_from_env(env, state=project_state)
        assert config.ssh_private_key_path == override

    def test_ssh_path_missing_in_state_and_env(self, project_state):
        project_state.config.ssh.private_key_path = None
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(_required_env(), state=project_state)

    def test_ssh_path_does_not_exist(self, project_state, tmp_path):
        env = _required_env(SSH_PRIVATE_KEY_PATH=str(tmp_path / "nope.pem"))
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)


# ---------------------------------------------------------------------------
# Optional defaults
# ---------------------------------------------------------------------------

class TestOptionalDefaults:
    def test_all_defaults_applied(self, project_state):
        config = build_setup_config_from_env(_required_env(), state=project_state)
        assert config.ssh_user == "ubuntu"
        assert config.edx_version == "sumac"
        # CLI_OPS_RELEASE_TAG unset -> resolved from the prod-images pin
        assert config.cli_ops_release_tag == CLI_OPS_TEST_TAG
        assert config.prod_images_tag == "main"
        assert config.enable_ai is True
        assert config.create_playwright_platforms is False
        assert config.platform_name == "main"
        assert config.github_org == "iblai"
        # NODE_ID unset -> empty; AnsibleRunner falls back to the project name
        assert config.node_id == ""

    def test_node_id_from_env(self, project_state):
        env = _required_env(NODE_ID="custom-node")
        config = build_setup_config_from_env(env, state=project_state)
        assert config.node_id == "custom-node"

    def test_enable_ai_explicit_false(self, project_state):
        env = _required_env(ENABLE_AI="false")
        config = build_setup_config_from_env(env, state=project_state)
        assert config.enable_ai is False

    def test_create_playwright_platforms_parsed_as_bool(self, project_state):
        env = _required_env(CREATE_PLAYWRIGHT_PLATFORMS="true")
        config = build_setup_config_from_env(env, state=project_state)
        assert config.create_playwright_platforms is True

    def test_platform_name_lowercased(self, project_state):
        env = _required_env(PLATFORM_NAME="MyTenant")
        config = build_setup_config_from_env(env, state=project_state)
        assert config.platform_name == "mytenant"


# ---------------------------------------------------------------------------
# Integration triggers
# ---------------------------------------------------------------------------

class TestIntegrationTriggers:
    def test_smtp_disabled_when_host_blank(self, project_state):
        config = build_setup_config_from_env(_required_env(), state=project_state)
        assert config.smtp_enabled is False

    def test_smtp_enabled_when_host_set(self, project_state):
        env = _required_env(
            SMTP_HOST="email-smtp.us-east-1.amazonaws.com",
            SMTP_PORT="465",
            SMTP_USERNAME="user",
            SMTP_PASSWORD="pass",
            SMTP_SENDER_EMAIL="noreply@example.com",
            SMTP_USE_SSL="true",
            SMTP_USE_TLS="false",
        )
        config = build_setup_config_from_env(env, state=project_state)
        assert config.smtp_enabled is True
        assert config.smtp_host == "email-smtp.us-east-1.amazonaws.com"
        assert config.smtp_port == 465
        assert config.smtp_use_ssl is True
        assert config.smtp_use_tls is False

    def test_stripe_trigger(self, project_state):
        env = _required_env(
            STRIPE_SECRET_KEY="sk_test_abc",
            STRIPE_PUB_KEY="pk_test_def",
            STRIPE_MODE="live",
        )
        config = build_setup_config_from_env(env, state=project_state)
        assert config.stripe_enabled is True
        assert config.stripe_secret_key == "sk_test_abc"
        assert config.stripe_mode == "live"

    def test_google_sso_trigger(self, project_state):
        env = _required_env(
            GOOGLE_SSO_CLIENT_ID="abc.apps.googleusercontent.com",
            GOOGLE_SSO_CLIENT_SECRET="test-google-secret",
        )
        config = build_setup_config_from_env(env, state=project_state)
        assert config.google_sso_enabled is True
        assert config.google_sso_client_id == "abc.apps.googleusercontent.com"

    def test_microsoft_sso_trigger(self, project_state):
        env = _required_env(
            MICROSOFT_SSO_CLIENT_ID="00000000-0000-0000-0000-000000000000",
            MICROSOFT_SSO_TENANT_ID="11111111-1111-1111-1111-111111111111",
        )
        config = build_setup_config_from_env(env, state=project_state)
        assert config.microsoft_sso_enabled is True
        assert config.microsoft_sso_tenant_id == "11111111-1111-1111-1111-111111111111"

    def test_smtp_port_invalid_int_raises(self, project_state):
        env = _required_env(SMTP_HOST="smtp.example.com", SMTP_PORT="abc")
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)

    def test_smtp_port_custom_value(self, project_state):
        env = _required_env(SMTP_HOST="smtp.example.com", SMTP_PORT="2525")
        config = build_setup_config_from_env(env, state=project_state)
        assert config.smtp_port == 2525


# ---------------------------------------------------------------------------
# Free-standing mode (build_bootstrap_state_from_env + build_setup_config)
# ---------------------------------------------------------------------------

class TestFreeStandingMode:
    def test_minimal_freestanding_env(self, ssh_key):
        state = build_bootstrap_state_from_env(_freestanding_env(ssh_key))
        assert state.name == "freestand"
        assert state.provider == "bootstrap"
        assert state.status == "created"
        assert state.outputs["instance_public_ip"] == "203.0.113.42"
        assert state.config.dns.base_domain == "free.example.com"

        config = build_setup_config_from_env(_freestanding_env(ssh_key), state=state)
        assert config.target_host == "203.0.113.42"
        assert config.base_domain == "free.example.com"

    def test_freestanding_missing_target_host(self, ssh_key):
        env = _freestanding_env(ssh_key)
        del env["TARGET_HOST"]
        with pytest.raises(typer.Exit):
            build_bootstrap_state_from_env(env)

    def test_freestanding_missing_ssh_path(self, ssh_key):
        env = _freestanding_env(ssh_key)
        del env["SSH_PRIVATE_KEY_PATH"]
        with pytest.raises(typer.Exit):
            build_bootstrap_state_from_env(env)

    def test_freestanding_missing_base_domain(self, ssh_key):
        env = _freestanding_env(ssh_key)
        del env["BASE_DOMAIN"]
        with pytest.raises(typer.Exit):
            build_bootstrap_state_from_env(env)

    def test_freestanding_missing_project_name(self, ssh_key):
        env = _freestanding_env(ssh_key)
        del env["PROJECT_NAME"]
        with pytest.raises(typer.Exit):
            build_bootstrap_state_from_env(env)

    def test_freestanding_invalid_project_name(self, ssh_key):
        env = _freestanding_env(ssh_key, PROJECT_NAME="Bad Name!")
        with pytest.raises(typer.Exit):
            build_bootstrap_state_from_env(env)

    def test_freestanding_ssh_key_not_found(self, tmp_path):
        env = _freestanding_env(tmp_path / "nope.pem")
        with pytest.raises(typer.Exit):
            build_bootstrap_state_from_env(env)

    def test_freestanding_aws_region_default(self, ssh_key):
        state = build_bootstrap_state_from_env(_freestanding_env(ssh_key))
        config = build_setup_config_from_env(_freestanding_env(ssh_key), state=state)
        assert config.aws_default_region == "us-east-1"

    def test_freestanding_aws_region_from_env(self, ssh_key):
        env = _freestanding_env(ssh_key, AWS_DEFAULT_REGION="ap-south-1")
        state = build_bootstrap_state_from_env(env)
        config = build_setup_config_from_env(env, state=state)
        assert config.aws_default_region == "ap-south-1"

    def test_freestanding_resumes_existing_bootstrap_state(self, ssh_key, project_state):
        # Pretend an existing bootstrap state already exists for this name.
        project_state.name = "freestand"
        project_state.provider = "bootstrap"
        project_state.setup_status = "completed"
        with patch("iblai_infra.env_setup.load_state", return_value=project_state):
            state = build_bootstrap_state_from_env(_freestanding_env(ssh_key))
        assert state is project_state  # reused, not clobbered
        assert state.setup_status == "completed"


# ---------------------------------------------------------------------------
# CLI_OPS_RELEASE_TAG resolution
# ---------------------------------------------------------------------------

class TestCliOpsTagResolution:
    def test_explicit_tag_bypasses_resolver(self, project_state, _pin_resolver):
        config = build_setup_config_from_env(
            _required_env(CLI_OPS_RELEASE_TAG="4.0.0"), state=project_state
        )
        assert config.cli_ops_release_tag == "4.0.0"
        _pin_resolver.assert_not_called()

    def test_resolver_called_with_prod_images_tag(self, project_state, _pin_resolver):
        config = build_setup_config_from_env(
            _required_env(PROD_IMAGES_TAG="1.64.0"), state=project_state
        )
        assert config.cli_ops_release_tag == CLI_OPS_TEST_TAG
        _pin_resolver.assert_called_once_with(
            "test-pat-value", "iblai", "iblai-prod-images", "1.64.0", subdir=None
        )

    def test_unreadable_pin_falls_back_to_main(self, project_state, _pin_resolver):
        _pin_resolver.return_value = None
        config = build_setup_config_from_env(_required_env(), state=project_state)
        assert config.cli_ops_release_tag == "main"


# ---------------------------------------------------------------------------
# S3 storage buckets
# ---------------------------------------------------------------------------


class TestS3Buckets:
    def test_both_buckets_set_storage_with_the_setup_region(self, project_state):
        env = _required_env(S3_STATIC_BUCKET="acme-dm-static", S3_MEDIA_BUCKET="acme-dm-media")
        config = build_setup_config_from_env(env, state=project_state)
        assert (config.s3_static_bucket, config.s3_media_bucket) == ("acme-dm-static", "acme-dm-media")
        assert config.s3_region == config.aws_default_region

    def test_explicit_region_wins(self, project_state):
        env = _required_env(S3_STATIC_BUCKET="acme-dm-static", S3_MEDIA_BUCKET="acme-dm-media", S3_REGION="eu-west-1")
        assert build_setup_config_from_env(env, state=project_state).s3_region == "eu-west-1"

    def test_no_buckets_leaves_storage_alone(self, project_state):
        config = build_setup_config_from_env(_required_env(S3_REGION="eu-west-1"), state=project_state)
        assert (config.s3_static_bucket, config.s3_media_bucket, config.s3_region) == ("", "", "")

    @pytest.mark.parametrize("key", ["S3_STATIC_BUCKET", "S3_MEDIA_BUCKET"])
    def test_one_bucket_alone_is_rejected(self, project_state, key):
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(_required_env(**{key: "acme-bucket"}), state=project_state)

    def test_invalid_bucket_name_is_rejected(self, project_state):
        env = _required_env(S3_STATIC_BUCKET="Acme_Static", S3_MEDIA_BUCKET="acme-dm-media")
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)


class TestLLMKey:
    def test_an_openrouter_key_is_the_gateway_provider(self, project_state):
        config = build_setup_config_from_env(_required_env(OPENROUTER_API_KEY="sk-or-v1-example"), state=project_state)
        assert (config.llm_provider, config.llm_api_key) == (LLMProvider.OPENROUTER, "sk-or-v1-example")

    def test_an_openai_key_still_works(self, project_state):
        config = build_setup_config_from_env(_required_env(OPENAI_API_KEY="sk-example"), state=project_state)
        assert (config.llm_provider, config.llm_api_key) == (LLMProvider.OPENAI, "sk-example")

    def test_both_keys_are_refused(self, project_state):
        env = _required_env(OPENAI_API_KEY="sk-example", OPENROUTER_API_KEY="sk-or-v1-example")
        with pytest.raises(typer.Exit):
            build_setup_config_from_env(env, state=project_state)
