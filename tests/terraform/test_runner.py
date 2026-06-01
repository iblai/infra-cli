"""Tests for iblai_infra.terraform.runner — JSON parsing, labels, and helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from iblai_infra.models import (
    CallServerConfig,
    CertMethod,
    CertificateConfig,
    DeploymentType,
    Environment,
    SSHKeyMethod,
    WAFConfig,
)
from iblai_infra.terraform.runner import (
    RESOURCE_LABELS,
    TerraformRunner,
    _friendly_label,
    _is_data_source,
)


# ---------------------------------------------------------------------------
# _friendly_label
# ---------------------------------------------------------------------------


class TestFriendlyLabel:
    def test_known_resource_type(self):
        assert _friendly_label("aws_vpc.main") == "VPC (main)"

    def test_resource_with_index(self):
        assert _friendly_label("aws_subnet.public[0]") == "Subnet (public)"

    def test_unknown_resource_type(self):
        result = _friendly_label("aws_custom_thing.foo")
        assert result == "aws_custom_thing (foo)"

    def test_single_part_address(self):
        assert _friendly_label("orphan") == "orphan"

    def test_all_known_types(self):
        for resource_type, label in RESOURCE_LABELS.items():
            result = _friendly_label(f"{resource_type}.test")
            assert result == f"{label} (test)"

    def test_security_group_rule(self):
        assert _friendly_label("aws_security_group_rule.ssh_inbound") == "Security Group Rule (ssh_inbound)"

    def test_lb_listener_certificate(self):
        assert _friendly_label("aws_lb_listener_certificate.https") == "ALB Certificate (https)"


# ---------------------------------------------------------------------------
# _is_data_source
# ---------------------------------------------------------------------------


class TestIsDataSource:
    def test_data_source(self):
        assert _is_data_source("data.aws_ami.ubuntu") is True

    def test_data_availability_zones(self):
        assert _is_data_source("data.aws_availability_zones.available") is True

    def test_managed_resource(self):
        assert _is_data_source("aws_vpc.main") is False

    def test_empty_string(self):
        assert _is_data_source("") is False

    def test_partial_match(self):
        assert _is_data_source("datastore.bucket") is False


# ---------------------------------------------------------------------------
# TerraformRunner._parse_json_line
# ---------------------------------------------------------------------------


class TestParseJsonLine:
    def test_valid_json(self):
        line = '{"type": "apply_start", "hook": {}}'
        result = TerraformRunner._parse_json_line(line)
        assert result == {"type": "apply_start", "hook": {}}

    def test_empty_line(self):
        assert TerraformRunner._parse_json_line("") is None

    def test_whitespace_only(self):
        assert TerraformRunner._parse_json_line("   \n") is None

    def test_invalid_json(self):
        assert TerraformRunner._parse_json_line("not json at all") is None

    def test_partial_json(self):
        assert TerraformRunner._parse_json_line('{"incomplete": ') is None

    def test_json_with_whitespace(self):
        line = '  {"key": "value"}  \n'
        result = TerraformRunner._parse_json_line(line)
        assert result == {"key": "value"}

    def test_nested_json(self):
        event = {"type": "diagnostic", "diagnostic": {"severity": "error", "summary": "fail"}}
        line = json.dumps(event)
        result = TerraformRunner._parse_json_line(line)
        assert result["diagnostic"]["severity"] == "error"


# ---------------------------------------------------------------------------
# TerraformRunner._env
# ---------------------------------------------------------------------------


class TestTerraformEnv:
    def test_profile_credentials(self, infra_config):
        infra_config.credentials.method = "profile"
        infra_config.credentials.profile = "myprofile"
        infra_config.credentials.access_key_id = None
        infra_config.credentials.secret_access_key = None

        with patch("iblai_infra.terraform.runner.workspace_dir", return_value=Path("/tmp/ws")):
            runner = TerraformRunner.__new__(TerraformRunner)
            runner.config = infra_config
            env = runner._env()
            assert env["AWS_PROFILE"] == "myprofile"
            assert env["AWS_DEFAULT_REGION"] == "us-east-1"
            assert env["TF_INPUT"] == "0"
            assert "AWS_ACCESS_KEY_ID" not in env

    def test_access_key_credentials(self, infra_config):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        env = runner._env()
        assert env["AWS_ACCESS_KEY_ID"] == "AKIAIOSFODNN7EXAMPLE"
        assert env["AWS_SECRET_ACCESS_KEY"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert env["AWS_DEFAULT_REGION"] == "us-east-1"

    def test_tf_input_disabled(self, infra_config):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        env = runner._env()
        assert env["TF_INPUT"] == "0"


# ---------------------------------------------------------------------------
# TerraformRunner._generate_tfvars
# ---------------------------------------------------------------------------


class TestGenerateTfvars:
    def test_basic_tfvars(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'project_name = "testproject"' in tfvars
        assert 'environment = "dev"' in tfvars
        assert 'region = "us-east-1"' in tfvars
        assert 'instance_type = "t3.2xlarge"' in tfvars
        assert "root_volume_size = 100" in tfvars
        assert 'base_domain = "example.com"' in tfvars
        assert "create_key_pair = true" in tfvars

    def test_bucket_suffix_included(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value="15012025"):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'bucket_suffix = "15012025"' in tfvars

    def test_no_bucket_suffix(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "bucket_suffix" not in tfvars

    def test_aws_keypair_mode(self, infra_config, tmp_path):
        infra_config.ssh.method = SSHKeyMethod.AWS_KEYPAIR
        infra_config.ssh.key_name = "existing-key"

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'existing_key_pair_name = "existing-key"' in tfvars
        assert "create_key_pair = false" in tfvars

    def test_cert_upload_writes_files(self, infra_config, tmp_path):
        infra_config.certificates = CertificateConfig(
            method=CertMethod.UPLOAD,
            cert_body="-----BEGIN CERTIFICATE-----\nMIIB...",
            cert_private_key="-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
            cert_chain="-----BEGIN CERTIFICATE-----\nMIIG...",
        )

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        assert (tmp_path / "cert.pem").exists()
        assert (tmp_path / "cert-key.pem").exists()
        assert (tmp_path / "cert-chain.pem").exists()
        assert "BEGIN CERTIFICATE" in (tmp_path / "cert.pem").read_text()

    def test_cert_acm_mode(self, infra_config, tmp_path):
        infra_config.certificates = CertificateConfig(
            method=CertMethod.ACM,
            hosted_zone_id="Z12345",
        )

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'certificate_method = "acm"' in tfvars
        assert 'hosted_zone_id = "Z12345"' in tfvars

    def test_cert_none_mode(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'certificate_method = "none"' in tfvars

    def test_ami_id_included(self, infra_config, tmp_path):
        infra_config.compute.ami_id = "ami-0123456789abcdef0"

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'ami_id = "ami-0123456789abcdef0"' in tfvars
        assert "skip_user_data = true" in tfvars

    def test_no_ami_id_by_default(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "ami_id" not in tfvars
        assert "skip_user_data" not in tfvars


class TestCopyTemplatesCallServer:
    def test_call_server_picks_correct_template_dir(self, infra_config, tmp_path):
        infra_config.deployment_type = DeploymentType.CALL
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        runner._copy_templates()

        # Expected files from templates/aws/call-server/
        for name in ("main.tf", "variables.tf", "outputs.tf", "user_data.sh"):
            assert (tmp_path / name).exists(), f"missing {name}"

        # main.tf should be the call-server one, not single-server — look for a
        # signature string that only exists in the call template
        main_tf = (tmp_path / "main.tf").read_text()
        assert "LiveKit" in main_tf or "call-sg" in main_tf

    def test_single_server_picks_correct_template_dir(self, infra_config, tmp_path):
        # Sanity: default SINGLE still copies the single-server template
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        runner._copy_templates()

        main_tf = (tmp_path / "main.tf").read_text()
        assert "LiveKit" not in main_tf


class TestGenerateTfvarsCallServer:
    """Call-server has its own variable set — no bucket_suffix, no certificate_method,
    no multi_server vars. Drives conditional DNS A-record via hosted_zone_id + enable_sip."""

    def test_emits_call_vars_and_skips_non_call(self, infra_config, tmp_path):
        infra_config.deployment_type = DeploymentType.CALL
        infra_config.call_server = CallServerConfig(
            instance_type="t3.large",
            volume_size=40,
            vpc_cidr="10.1.0.0/16",
            enable_sip=True,
        )
        # Align the shared compute config with call defaults (the CLI does this)
        infra_config.compute.instance_type = "t3.large"
        infra_config.compute.volume_size = 40
        infra_config.network.vpc_cidr = "10.1.0.0/16"
        infra_config.certificates = CertificateConfig(
            method=CertMethod.ACM, hosted_zone_id="Z12345"
        )

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        # Bucket suffix lookup is AWS-side; it must NOT be called for call-server.
        with patch.object(runner, "_resolve_bucket_suffix") as mock_bucket:
            runner._generate_tfvars()
            mock_bucket.assert_not_called()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        # Core shared vars still emitted
        assert 'project_name = "testproject"' in tfvars
        assert 'instance_type = "t3.large"' in tfvars
        assert "root_volume_size = 40" in tfvars
        assert 'vpc_cidr = "10.1.0.0/16"' in tfvars
        # Call-specific
        assert 'hosted_zone_id = "Z12345"' in tfvars
        assert "enable_sip = true" in tfvars
        # Non-call vars must be absent (they'd be undeclared in the call template)
        assert "certificate_method" not in tfvars
        assert "bucket_suffix" not in tfvars
        assert "app_server_count" not in tfvars
        assert "enable_mysql" not in tfvars

    def test_enable_sip_false_by_default(self, infra_config, tmp_path):
        infra_config.deployment_type = DeploymentType.CALL
        infra_config.call_server = CallServerConfig()
        infra_config.certificates = CertificateConfig(method=CertMethod.NONE)

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        runner._generate_tfvars()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "enable_sip = false" in tfvars
        # empty hosted_zone_id is still written (so terraform can read ""), but with no cert the R53 A record will be skipped
        assert 'hosted_zone_id = ""' in tfvars


# ---------------------------------------------------------------------------
# TerraformRunner._generate_tfvars — WAF
# ---------------------------------------------------------------------------


class TestGenerateTfvarsWAF:
    """WAF tfvars are emitted only for single-server. Disabled by default."""

    def test_no_waf_emits_enable_false(self, infra_config, tmp_path):
        # single-server (the fixture default), no waf
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "enable_waf = false" in tfvars
        assert "waf_allowed_ips" not in tfvars

    def test_waf_enabled_emits_ip_list(self, infra_config, tmp_path):
        infra_config.waf = WAFConfig(
            enabled=True,
            allowed_ips=["203.0.113.7", "10.0.0.0/16"],
        )
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "enable_waf = true" in tfvars
        # list is JSON-encoded for HCL safety
        assert 'waf_allowed_ips = ["203.0.113.7/32", "10.0.0.0/16"]' in tfvars

    def test_multi_server_skips_waf_block_entirely(self, infra_config, tmp_path):
        # Even if a WAFConfig is attached, multi-server topology must NOT
        # emit `enable_waf` (the multi template has no such variable).
        from iblai_infra.models import MultiServerConfig

        infra_config.deployment_type = DeploymentType.MULTI
        infra_config.multi_server = MultiServerConfig()
        infra_config.waf = WAFConfig(enabled=True, allowed_ips=["203.0.113.7"])

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "enable_waf" not in tfvars
        assert "waf_allowed_ips" not in tfvars

    def test_call_server_skips_waf_block_entirely(self, infra_config, tmp_path):
        infra_config.deployment_type = DeploymentType.CALL
        infra_config.call_server = CallServerConfig()
        infra_config.certificates = CertificateConfig(method=CertMethod.NONE)
        # The CLI shouldn't construct this combo but the runner still gates
        # by deployment_type defensively.
        infra_config.waf = WAFConfig(enabled=True, allowed_ips=["203.0.113.7"])

        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        runner._generate_tfvars()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "enable_waf" not in tfvars
        assert "waf_allowed_ips" not in tfvars

    def test_tf_helper_handles_list_quoting(self, infra_config, tmp_path):
        """Embedded special chars (e.g. quotes) are safely JSON-encoded.

        IP/CIDR strings won't contain quotes today, but the helper's contract
        is "safe HCL list emission" so verify it stays robust.
        """
        infra_config.waf = WAFConfig(
            enabled=True,
            allowed_ips=["192.0.2.1", "198.51.100.0/24"],
        )
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value=""):
            runner._generate_tfvars()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        # All entries quoted; comma-separated; brackets present
        assert '"192.0.2.1/32"' in tfvars
        assert '"198.51.100.0/24"' in tfvars
        assert tfvars.count("waf_allowed_ips = [") == 1


# ---------------------------------------------------------------------------
# TerraformRunner._generate_tfvars — pinned bucket_suffix
# ---------------------------------------------------------------------------


class TestGenerateTfvarsBucketSuffixPinning:
    """The pinned-bucket_suffix path lets post-provision feature toggles
    re-emit tfvars without renaming S3 buckets. Critical safety guarantee."""

    def test_default_calls_aws(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix", return_value="15042026") as mock_resolve:
            runner._generate_tfvars()
            mock_resolve.assert_called_once_with(infra_config)

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'bucket_suffix = "15042026"' in tfvars

    def test_pinned_skips_aws(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix") as mock_resolve:
            runner._generate_tfvars(bucket_suffix="03012025")
            mock_resolve.assert_not_called()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        # Pinned value, NOT today's date
        assert 'bucket_suffix = "03012025"' in tfvars

    def test_pinned_empty_string_omits_line(self, infra_config, tmp_path):
        # An empty pinned suffix means "no suffix" (the original apply found
        # default bucket names available). The line must NOT appear at all
        # so terraform uses the variable's default of "".
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch.object(runner, "_resolve_bucket_suffix") as mock_resolve:
            runner._generate_tfvars(bucket_suffix="")
            mock_resolve.assert_not_called()

        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "bucket_suffix" not in tfvars


# ---------------------------------------------------------------------------
# TerraformRunner.reapply + _read_existing_bucket_suffix
# ---------------------------------------------------------------------------


class TestReadExistingBucketSuffix:
    def test_reads_quoted_value(self, infra_config, tmp_path):
        (tmp_path / "terraform.tfvars").write_text(
            'project_name = "x"\nbucket_suffix = "31122025"\n'
        )
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path
        assert runner._read_existing_bucket_suffix() == "31122025"

    def test_missing_line_returns_empty(self, infra_config, tmp_path):
        (tmp_path / "terraform.tfvars").write_text(
            'project_name = "x"\n'
        )
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path
        # Empty string (NOT None) means "first apply had no suffix; keep it
        # that way" — pinning to "" prevents an unexpected suffix on re-apply.
        assert runner._read_existing_bucket_suffix() == ""

    def test_no_file_returns_none(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path
        # No tfvars file at all — fall back to AWS resolution
        assert runner._read_existing_bucket_suffix() is None


class TestReapply:
    """reapply() is the load-bearing helper for post-provision feature toggles."""

    def _stub_runner(self, infra_config, ws):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = ws
        runner.state = MagicMock()
        return runner

    def test_missing_workspace_raises(self, infra_config, tmp_path):
        runner = self._stub_runner(infra_config, tmp_path / "nope")
        with patch.object(runner, "_check_terraform_installed"):
            with pytest.raises(FileNotFoundError):
                runner.reapply()

    def test_no_main_tf_raises(self, infra_config, tmp_path):
        runner = self._stub_runner(infra_config, tmp_path)
        # tmp_path exists but no main.tf
        with patch.object(runner, "_check_terraform_installed"):
            with pytest.raises(FileNotFoundError):
                runner.reapply()

    def test_happy_path_pins_suffix_and_runs(self, infra_config, tmp_path):
        # Pre-populate workspace with main.tf + existing tfvars carrying a suffix
        (tmp_path / "main.tf").write_text("# stub")
        (tmp_path / "terraform.tfvars").write_text(
            'project_name = "x"\nbucket_suffix = "29022024"\n'
        )
        runner = self._stub_runner(infra_config, tmp_path)
        runner.config.waf = WAFConfig(enabled=True, allowed_ips=["203.0.113.7"])

        with patch.object(runner, "_check_terraform_installed"), \
             patch.object(runner, "_copy_templates") as mock_copy, \
             patch.object(runner, "_resolve_bucket_suffix") as mock_resolve, \
             patch.object(runner, "init") as mock_init, \
             patch.object(runner, "plan", return_value=3) as mock_plan, \
             patch.object(runner, "apply", return_value={"waf_web_acl_arn": "arn:..."}) as mock_apply:
            outputs = runner.reapply()

        # The pinned suffix path: NEVER calls AWS-side resolution
        mock_resolve.assert_not_called()
        # All the phases ran in order
        mock_copy.assert_called_once()
        mock_init.assert_called_once()
        mock_plan.assert_called_once()
        mock_apply.assert_called_once()
        # Outputs from apply propagate to caller
        assert outputs == {"waf_web_acl_arn": "arn:..."}

        # tfvars was regenerated from state.config (now includes WAF)
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "enable_waf = true" in tfvars
        assert '"203.0.113.7/32"' in tfvars
        # AND the original bucket_suffix is preserved
        assert 'bucket_suffix = "29022024"' in tfvars

    def test_noop_plan_skips_apply_returns_existing_outputs(self, infra_config, tmp_path):
        (tmp_path / "main.tf").write_text("# stub")
        (tmp_path / "terraform.tfvars").write_text('bucket_suffix = ""\n')
        runner = self._stub_runner(infra_config, tmp_path)

        with patch.object(runner, "_check_terraform_installed"), \
             patch.object(runner, "_copy_templates"), \
             patch.object(runner, "init"), \
             patch.object(runner, "plan", return_value=0), \
             patch.object(runner, "apply") as mock_apply, \
             patch.object(runner, "_get_outputs", return_value={"alb_dns_name": "alb-1"}):
            outputs = runner.reapply()

        # plan returned 0 → apply must NOT run
        mock_apply.assert_not_called()
        # Caller still gets the current outputs so it can refresh state
        assert outputs == {"alb_dns_name": "alb-1"}


# ---------------------------------------------------------------------------
# TerraformRunner._resolve_bucket_suffix
# ---------------------------------------------------------------------------


class TestResolveBucketSuffix:
    def test_bucket_available(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch("iblai_infra.providers.aws.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value = mock_session
            # head_bucket raises 404 = bucket does not exist
            mock_session.client.return_value.head_bucket.side_effect = ClientError(
                {"Error": {"Code": "404"}}, "HeadBucket"
            )
            suffix = runner._resolve_bucket_suffix(infra_config)
            assert suffix == ""

    def test_bucket_taken(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch("iblai_infra.providers.aws.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value = mock_session
            # head_bucket succeeds = bucket exists
            mock_session.client.return_value.head_bucket.return_value = {}
            suffix = runner._resolve_bucket_suffix(infra_config)
            assert len(suffix) == 8  # DDMMYYYY
            assert suffix.isdigit()

    def test_exception_returns_empty(self, infra_config, tmp_path):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config
        runner.ws = tmp_path

        with patch("iblai_infra.providers.aws.get_session", side_effect=Exception("boom")):
            suffix = runner._resolve_bucket_suffix(infra_config)
            assert suffix == ""


# ---------------------------------------------------------------------------
# TerraformRunner._check_terraform_installed
# ---------------------------------------------------------------------------


class TestCheckTerraformInstalled:
    def test_found(self, infra_config):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config

        with patch("iblai_infra.terraform.runner.shutil.which", return_value="/usr/bin/terraform"):
            runner._check_terraform_installed()  # Should not raise

    def test_not_found(self, infra_config):
        runner = TerraformRunner.__new__(TerraformRunner)
        runner.config = infra_config

        with (
            patch("iblai_infra.terraform.runner.shutil.which", return_value=None),
            pytest.raises(SystemExit),
        ):
            runner._check_terraform_installed()


# ---------------------------------------------------------------------------
# Apply event parsing (integration-style)
# ---------------------------------------------------------------------------


class TestApplyEventParsing:
    """Test the event parsing logic used during apply/destroy."""

    def test_apply_start_event(self):
        event = {
            "type": "apply_start",
            "hook": {
                "resource": {"addr": "aws_vpc.main"},
                "action": "create",
            },
        }
        addr = event["hook"]["resource"]["addr"]
        label = _friendly_label(addr)
        assert label == "VPC (main)"

    def test_apply_complete_event(self):
        event = {
            "type": "apply_complete",
            "hook": {
                "resource": {"addr": "aws_instance.server"},
                "elapsed_seconds": 42,
            },
        }
        assert event["hook"]["elapsed_seconds"] == 42

    def test_apply_errored_event(self):
        event = {
            "type": "apply_errored",
            "hook": {
                "resource": {"addr": "aws_s3_bucket.backups"},
            },
        }
        addr = event.get("hook", {}).get("resource", {}).get("addr", "unknown")
        assert addr == "aws_s3_bucket.backups"
        # Error detail should NOT be extracted from apply_errored
        assert "diagnostic" not in event

    def test_diagnostic_event_error(self):
        event = {
            "type": "diagnostic",
            "diagnostic": {
                "severity": "error",
                "summary": "BucketAlreadyExists",
                "detail": "The bucket name is already taken",
            },
        }
        diag = event["diagnostic"]
        summary = diag.get("summary", "")
        detail = diag.get("detail", "")
        msg = f"{summary}: {detail}" if summary and detail else (summary or detail or "Unknown error")
        assert msg == "BucketAlreadyExists: The bucket name is already taken"

    def test_diagnostic_summary_only(self):
        event = {
            "type": "diagnostic",
            "diagnostic": {"severity": "error", "summary": "Something failed", "detail": ""},
        }
        diag = event["diagnostic"]
        summary = diag.get("summary", "")
        detail = diag.get("detail", "")
        msg = f"{summary}: {detail}" if summary and detail else (summary or detail or "Unknown error")
        assert msg == "Something failed"

    def test_diagnostic_no_info(self):
        event = {
            "type": "diagnostic",
            "diagnostic": {"severity": "error", "summary": "", "detail": ""},
        }
        diag = event["diagnostic"]
        summary = diag.get("summary", "")
        detail = diag.get("detail", "")
        msg = f"{summary}: {detail}" if summary and detail else (summary or detail or "Unknown error")
        assert msg == "Unknown error"

    def test_change_summary_event(self):
        event = {
            "type": "change_summary",
            "changes": {"add": 15, "change": 0, "remove": 0},
        }
        changes = event.get("changes", {})
        total = changes.get("add", 0) + changes.get("change", 0) + changes.get("remove", 0)
        assert total == 15

    def test_destroy_filters_data_sources(self):
        """Data sources like data.aws_ami should be filtered during destroy."""
        addrs = [
            "data.aws_ami.ubuntu",
            "data.aws_availability_zones.available",
            "aws_vpc.main",
            "aws_instance.server",
        ]
        filtered = [a for a in addrs if not _is_data_source(a)]
        assert len(filtered) == 2
        assert "aws_vpc.main" in filtered
        assert "aws_instance.server" in filtered
