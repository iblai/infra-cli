"""``iblai infra s3 check|fix`` and the bucket gate in ``setup-env``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from iblai_infra.cli import app
from iblai_infra.s3_buckets import BucketFixError, BucketReport, BucketRole, Finding

runner = CliRunner()


def _report(*failing: str) -> BucketReport:
    checks = [("acme-dm-static", BucketRole.STATIC, "cors"), ("acme-dm-static", BucketRole.STATIC, "public-read"),
              ("acme-dm-media", BucketRole.MEDIA, "cors"), ("acme-dm-media", BucketRole.MEDIA, "private")]
    return BucketReport([Finding(b, r, c, f"{r.value}:{c}" not in failing, "detail") for b, r, c in checks])


@pytest.fixture
def env_file(tmp_path) -> Path:
    f = tmp_path / ".env"
    f.write_text(
        "BASE_DOMAIN=example.com\nS3_STATIC_BUCKET=acme-dm-static\nS3_MEDIA_BUCKET=acme-dm-media\n"
        "S3_REGION=us-east-2\nS3_ACCESS_KEY_ID=AKIAS3EXAMPLE\nS3_SECRET_ACCESS_KEY=s3-secret\n"
        "AWS_ACCESS_KEY_ID=AKIAECREXAMPLE\nAWS_SECRET_ACCESS_KEY=ecr-secret\n"
    )
    return f


class TestCheck:
    def test_ok_exits_zero(self, env_file):
        with patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report()):
            result = runner.invoke(app, ["infra", "s3", "check", "-f", str(env_file)])
        assert result.exit_code == 0, result.output
        assert "configured the way the platform needs" in result.output

    def test_a_problem_exits_non_zero_and_names_the_fix(self, env_file):
        with patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report("media:cors")):
            result = runner.invoke(app, ["infra", "s3", "check", "-f", str(env_file)])
        assert result.exit_code == 1
        assert "FAIL" in result.output and "iblai infra s3 fix" in result.output

    def test_uses_the_s3_key_not_the_ecr_key(self, env_file):
        with patch("iblai_infra.s3_cli.boto3.Session") as session, \
             patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report()) as check:
            runner.invoke(app, ["infra", "s3", "check", "-f", str(env_file)])
        assert session.call_args.kwargs["aws_access_key_id"] == "AKIAS3EXAMPLE"
        assert check.call_args.kwargs["base_domain"] == "example.com"
        assert check.call_args.kwargs["region"] == "us-east-2"

    def test_a_profile_overrides_the_env_key(self, env_file):
        with patch("iblai_infra.s3_cli.boto3.Session") as session, \
             patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report()):
            runner.invoke(app, ["infra", "s3", "check", "-f", str(env_file), "--profile", "admin"])
        assert session.call_args.kwargs == {"profile_name": "admin", "region_name": "us-east-2"}

    def test_missing_inputs_are_named(self, tmp_path):
        result = runner.invoke(app, ["infra", "s3", "check", "--static", "acme-dm-static"])
        assert result.exit_code == 1
        assert "media bucket" in result.output and "base domain" in result.output


class TestFix:
    def test_repairs_after_confirmation_and_checks_again(self, env_file):
        with patch("iblai_infra.s3_cli.check_dm_buckets", side_effect=[_report("media:cors"), _report()]), \
             patch("iblai_infra.s3_cli.ensure_dm_buckets", return_value=["acme-dm-media: added the media CORS rule"]) as ensure:
            result = runner.invoke(app, ["infra", "s3", "fix", "-f", str(env_file), "--yes",
                                         "--extra-origin", "https://tenant.example.org"])
        assert result.exit_code == 0, result.output
        assert ensure.call_args.kwargs["extra_origins"] == ("https://tenant.example.org",)
        assert "now configured" in result.output

    def test_declining_changes_nothing(self, env_file):
        with patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report("media:cors")), \
             patch("iblai_infra.s3_cli.ensure_dm_buckets") as ensure, \
             patch("questionary.confirm") as confirm:
            confirm.return_value.ask.return_value = False
            result = runner.invoke(app, ["infra", "s3", "fix", "-f", str(env_file)])
        assert result.exit_code != 0
        ensure.assert_not_called()

    def test_a_refused_repair_points_at_other_credentials(self, env_file):
        with patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report("media:cors")), \
             patch("iblai_infra.s3_cli.ensure_dm_buckets", side_effect=BucketFixError("acme-dm-media: PutBucketCors was refused")):
            result = runner.invoke(app, ["infra", "s3", "fix", "-f", str(env_file), "--yes"])
        assert result.exit_code == 1
        assert "PutBucketCors was refused" in result.output and "--profile" in result.output

    def test_nothing_to_fix(self, env_file):
        with patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report()), \
             patch("iblai_infra.s3_cli.ensure_dm_buckets") as ensure:
            result = runner.invoke(app, ["infra", "s3", "fix", "-f", str(env_file), "--yes"])
        assert result.exit_code == 0
        ensure.assert_not_called()


class TestSetupEnvGate:
    @pytest.fixture
    def setup_env_file(self, tmp_path) -> Path:
        key = tmp_path / "id_test"
        key.write_text("x")
        f = tmp_path / ".env"
        f.write_text(
            "PROJECT_NAME=gate\nTARGET_HOST=203.0.113.9\n"
            f"SSH_PRIVATE_KEY_PATH={key}\nBASE_DOMAIN=example.com\n"
            "AWS_ACCESS_KEY_ID=AKIAECREXAMPLE\nAWS_SECRET_ACCESS_KEY=ecr-secret\nGIT_TOKEN=pat\n"
            "CLI_OPS_RELEASE_TAG=7.24.0\nADMIN_USERNAME=platform_admin\nADMIN_EMAIL=admin@example.com\n"
            "ADMIN_PASSWORD=change-me-min-8\nS3_STATIC_BUCKET=acme-dm-static\nS3_MEDIA_BUCKET=acme-dm-media\n"
            "S3_ACCESS_KEY_ID=AKIAS3EXAMPLE\nS3_SECRET_ACCESS_KEY=s3-secret\n"
        )
        return f

    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch):
        monkeypatch.setattr("iblai_infra.env_setup.validate_key_permissions", lambda path: True)
        monkeypatch.setattr("iblai_infra.env_setup.save_state", lambda state: None)

    def _ansible(self):
        inst = MagicMock()
        inst.preflight.return_value = False  # stop right after the gate
        return inst

    def test_misconfigured_buckets_stop_setup_before_the_server_is_touched(self, setup_env_file):
        inst = self._ansible()
        with patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report("media:cors")), \
             patch("iblai_infra.ansible.runner.AnsibleRunner", return_value=inst) as runner_cls:
            result = runner.invoke(app, ["infra", "setup-env", "-f", str(setup_env_file)])
        assert result.exit_code == 1
        assert "iblai infra s3 fix" in result.output
        runner_cls.assert_not_called()

    def test_correct_buckets_let_setup_continue(self, setup_env_file):
        inst = self._ansible()
        with patch("iblai_infra.s3_cli.check_dm_buckets", return_value=_report()) as check, \
             patch("iblai_infra.ansible.runner.AnsibleRunner", return_value=inst) as runner_cls:
            runner.invoke(app, ["infra", "setup-env", "-f", str(setup_env_file)])
        check.assert_called_once()
        runner_cls.assert_called_once()

    def test_fix_buckets_repairs_then_continues(self, setup_env_file):
        inst = self._ansible()
        with patch("iblai_infra.s3_cli.check_dm_buckets", side_effect=[_report("media:cors"), _report()]), \
             patch("iblai_infra.s3_cli.ensure_dm_buckets", return_value=["fixed"]) as ensure, \
             patch("iblai_infra.ansible.runner.AnsibleRunner", return_value=inst) as runner_cls:
            runner.invoke(app, ["infra", "setup-env", "-f", str(setup_env_file), "--fix-buckets"])
        ensure.assert_called_once()
        runner_cls.assert_called_once()

    def test_the_gate_can_be_skipped(self, setup_env_file):
        inst = self._ansible()
        with patch("iblai_infra.s3_cli.check_dm_buckets") as check, \
             patch("iblai_infra.ansible.runner.AnsibleRunner", return_value=inst):
            runner.invoke(app, ["infra", "setup-env", "-f", str(setup_env_file), "--skip-bucket-check"])
        check.assert_not_called()

    def test_no_buckets_no_check(self, setup_env_file):
        text = "\n".join(l for l in setup_env_file.read_text().splitlines() if not l.startswith("S3_")) + "\n"
        setup_env_file.write_text(text)
        inst = self._ansible()
        with patch("iblai_infra.s3_cli.check_dm_buckets") as check, \
             patch("iblai_infra.ansible.runner.AnsibleRunner", return_value=inst):
            runner.invoke(app, ["infra", "setup-env", "-f", str(setup_env_file)])
        check.assert_not_called()
