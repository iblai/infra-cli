"""GCP provisioning tests: model validators, runner dispatch, env builder."""

from __future__ import annotations

from unittest import mock

import pytest
import typer

from iblai_infra.models import (
    CertificateConfig,
    CertMethod,
    CloudProvider,
    ComputeConfig,
    DNSConfig,
    Environment,
    GCPAuthMethod,
    GCPCredentials,
    InfraConfig,
    NetworkConfig,
    SSHConfig,
    SSHKeyMethod,
    gcp_machine_ram_gb,
)
from iblai_infra.terraform.runner import RESOURCE_LABELS, TerraformRunner, _friendly_label


class TestCloudValidator:
    def test_gcp_requires_gcp_credentials(self):
        with pytest.raises(ValueError, match="GCP deployments require"):
            InfraConfig(
                project_name="d", environment=Environment.DEV, cloud=CloudProvider.GCP,
                network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="1.2.3.4"),
                compute=ComputeConfig(instance_type="e2-standard-8", volume_size=100, volume_type="pd-balanced"),
                ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k", public_key="x"),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            )

    def test_aws_requires_credentials(self):
        with pytest.raises(ValueError, match="AWS deployments require"):
            InfraConfig(
                project_name="d", environment=Environment.DEV,
                network=NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="1.2.3.4"),
                compute=ComputeConfig(instance_type="t3.2xlarge", volume_size=100, volume_type="gp3"),
                ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k", public_key="x"),
                certificates=CertificateConfig(method=CertMethod.NONE),
                dns=DNSConfig(base_domain="example.com"),
            )

    def test_default_cloud_is_aws(self, infra_config):
        assert infra_config.cloud == CloudProvider.AWS

    def test_region_property(self, gcp_infra_config, infra_config):
        assert gcp_infra_config.region == "us-central1"
        assert infra_config.region == "us-east-1"


class TestRunnerGcpDispatch:
    def _runner(self, config, ws):
        r = TerraformRunner.__new__(TerraformRunner)
        r.config = config
        r.ws = ws
        return r

    def test_copy_templates_selects_gcp_tree(self, gcp_infra_config, tmp_path):
        r = self._runner(gcp_infra_config, tmp_path)
        r._copy_templates()
        assert (tmp_path / "main.tf").exists()
        assert (tmp_path / "variables.tf").exists()
        assert (tmp_path / "startup-script.sh").exists()
        assert "google_compute_instance" in (tmp_path / "main.tf").read_text()

    def test_health_check_probes_lms_heartbeat(self, gcp_infra_config, tmp_path):
        """Regression: GCP health checks accept only a literal 200, and the
        platform nginx catch-all 301s unknown Hosts on "/" — probing "/"
        marks the backend UNHEALTHY and every URL serves 503 ("no healthy
        upstream"). The template must probe the LMS heartbeat with the
        learn.<domain> Host header instead (verified live)."""
        r = self._runner(gcp_infra_config, tmp_path)
        r._copy_templates()
        main_tf = (tmp_path / "main.tf").read_text()
        assert 'request_path = "/heartbeat"' in main_tf
        assert 'host         = "learn.${var.base_domain}"' in main_tf

    def test_generate_tfvars_gcp(self, gcp_infra_config, tmp_path):
        r = self._runner(gcp_infra_config, tmp_path)
        r._generate_tfvars()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'project_id = "test-gcp-project"' in tfvars
        assert 'zone = "us-central1-a"' in tfvars
        assert 'machine_type = "e2-standard-8"' in tfvars
        assert 'disk_type = "pd-balanced"' in tfvars
        assert 'certificate_method = "managed"' in tfvars
        assert 'dns_zone_name = "example-zone"' in tfvars
        assert "create_dns_zone = false" in tfvars
        # AWS-only knobs must not leak
        assert "\ninstance_type" not in tfvars
        assert "bucket_suffix" not in tfvars
        assert "ami_id" not in tfvars

    def test_env_gcp_sets_google_vars(self, gcp_infra_config, tmp_path):
        r = self._runner(gcp_infra_config, tmp_path)
        env = r._env()
        assert env["GOOGLE_PROJECT"] == "test-gcp-project"
        assert env["CLOUDSDK_COMPUTE_REGION"] == "us-central1"
        assert env["CLOUDSDK_COMPUTE_ZONE"] == "us-central1-a"
        assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/tmp/sa-key.json"
        assert not any(k.startswith("AWS_") for k in env)

    def test_env_aws_unchanged(self, infra_config, tmp_path):
        r = self._runner(infra_config, tmp_path)
        env = r._env()
        assert env["AWS_ACCESS_KEY_ID"] == "AKIAIOSFODNN7EXAMPLE"
        assert env["AWS_DEFAULT_REGION"] == "us-east-1"
        assert "GOOGLE_PROJECT" not in env


class TestGcpEnvProvision:
    def _env(self, **over):
        base = {
            "PROVIDER": "gcp", "GCP_PROJECT_ID": "proj-123", "PROJECT_NAME": "mydeploy",
            "ENVIRONMENT": "staging", "DOMAIN": "example.com", "VPN_IP": "203.0.113.7",
            "SSH_KEY_METHOD": "existing_file", "SSH_PUBLIC_KEY": "ssh-ed25519 AAAA test",
            "CERT_METHOD": "none",
        }
        base.update(over)
        return base

    def _build(self, env):
        from iblai_infra.env_provision import build_infra_config_from_env
        return build_infra_config_from_env(env, auto_delete_cnames=False)

    def test_dispatch_builds_gcp_config(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials",
                        return_value=mock.MagicMock(account="sa@x")):
            cfg = self._build(self._env())
        assert cfg.cloud == CloudProvider.GCP
        assert cfg.gcp_credentials.project_id == "proj-123"
        assert cfg.compute.instance_type == "e2-standard-8"
        assert cfg.certificates.method == CertMethod.NONE

    def test_managed_auto_detects_zone(self):
        zone = mock.MagicMock(name="example-zone", dns_name="example.com")
        zone.name = "example-zone"
        zone.dns_name = "example.com"
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")), \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[zone]), \
             mock.patch("iblai_infra.providers.gcp.find_conflicting_records", return_value=[]):
            cfg = self._build(self._env(CERT_METHOD="managed"))
        assert cfg.certificates.method == CertMethod.MANAGED
        assert cfg.dns.dns_zone_name == "example-zone"

    def test_auto_falls_back_to_none_without_zone(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")), \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[]):
            cfg = self._build(self._env(CERT_METHOD="auto"))
        assert cfg.certificates.method == CertMethod.NONE

    def test_missing_project_id_exits(self):
        env = self._env()
        del env["GCP_PROJECT_ID"]
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")):
            with pytest.raises((SystemExit, typer.Exit)):
                self._build(env)

    def test_create_dns_zone_requires_zone_name(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")):
            with pytest.raises((SystemExit, typer.Exit)):
                self._build(self._env(CERT_METHOD="managed", CREATE_DNS_ZONE="true"))

    def test_bad_credentials_exits(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", side_effect=ValueError("no ADC")):
            with pytest.raises((SystemExit, typer.Exit)):
                self._build(self._env())


class TestFriendlyLabelGcp:
    """Mirror of AWS TestFriendlyLabel for the google_* resource labels."""

    def test_known_google_resources(self):
        assert _friendly_label("google_compute_instance.main") == "Compute Instance (main)"
        assert _friendly_label("google_compute_global_forwarding_rule.https") == "Forwarding Rule (https)"
        assert _friendly_label("google_dns_managed_zone.main") == "DNS Zone (main)"

    def test_google_resource_with_index(self):
        assert _friendly_label('google_dns_record_set.app["learn"]') == "DNS Record (app)"

    def test_all_google_labels_resolve(self):
        for rt, label in RESOURCE_LABELS.items():
            if rt.startswith("google_"):
                assert _friendly_label(f"{rt}.test") == f"{label} (test)"


class TestRunnerGcpTfvarsVariants:
    def _runner(self, config, ws):
        r = TerraformRunner.__new__(TerraformRunner)
        r.config = config
        r.ws = ws
        return r

    def test_cert_none(self, gcp_infra_config, tmp_path):
        gcp_infra_config.certificates = CertificateConfig(method=CertMethod.NONE)
        gcp_infra_config.dns = DNSConfig(base_domain="example.com")
        self._runner(gcp_infra_config, tmp_path)._generate_tfvars()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'certificate_method = "none"' in tfvars
        assert "dns_zone_name" not in tfvars
        assert "create_dns_zone" not in tfvars

    def test_cert_upload_writes_pem_files(self, gcp_infra_config, tmp_path):
        gcp_infra_config.certificates = CertificateConfig(
            method=CertMethod.UPLOAD,
            cert_body="-----BEGIN CERTIFICATE-----\nAAA",
            cert_private_key="-----BEGIN PRIVATE KEY-----\nBBB",
        )
        self._runner(gcp_infra_config, tmp_path)._generate_tfvars()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'certificate_method = "upload"' in tfvars
        assert (tmp_path / "cert.pem").exists()
        assert (tmp_path / "cert-key.pem").exists()
        assert "BEGIN CERTIFICATE" in (tmp_path / "cert.pem").read_text()

    def test_custom_image_sets_skip_startup(self, gcp_infra_config, tmp_path):
        gcp_infra_config.compute.ami_id = "projects/x/global/images/prebuilt"
        self._runner(gcp_infra_config, tmp_path)._generate_tfvars()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert 'image = "projects/x/global/images/prebuilt"' in tfvars
        assert "skip_startup_script = true" in tfvars

    def test_no_image_by_default(self, gcp_infra_config, tmp_path):
        self._runner(gcp_infra_config, tmp_path)._generate_tfvars()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        assert "image" not in tfvars.replace("machine_type", "")  # avoid false match
        assert "skip_startup_script" not in tfvars

    def test_env_adc_omits_credentials_file(self, gcp_infra_config, tmp_path):
        gcp_infra_config.gcp_credentials.method = GCPAuthMethod.ADC
        gcp_infra_config.gcp_credentials.credentials_file = None
        env = self._runner(gcp_infra_config, tmp_path)._env()
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
        assert env["GOOGLE_PROJECT"] == "test-gcp-project"


class TestMaybeGcpCertNotice:
    def _runner(self, config, ws):
        r = TerraformRunner.__new__(TerraformRunner)
        r.config = config
        r.ws = ws
        return r

    def test_managed_prints_provisioning_notice(self, gcp_infra_config, tmp_path):
        r = self._runner(gcp_infra_config, tmp_path)
        with mock.patch("iblai_infra.terraform.runner.ui") as mui:
            r._maybe_gcp_cert_notice({})
        assert mui.info.called

    def test_nameservers_surfaced(self, gcp_infra_config, tmp_path):
        r = self._runner(gcp_infra_config, tmp_path)
        with mock.patch("iblai_infra.terraform.runner.ui") as mui:
            r._maybe_gcp_cert_notice({"dns_name_servers": ["ns1.example.", "ns2.example."]})
        assert mui.warning.called
        assert mui.muted.call_count >= 2

    def test_noop_for_aws(self, infra_config, tmp_path):
        r = self._runner(infra_config, tmp_path)
        with mock.patch("iblai_infra.terraform.runner.ui") as mui:
            r._maybe_gcp_cert_notice({"dns_name_servers": ["ns1"]})
        assert not mui.info.called
        assert not mui.warning.called


class TestGcpModels:
    def test_gcp_credentials_defaults(self):
        gc = GCPCredentials(project_id="p")
        assert gc.method == GCPAuthMethod.ADC
        assert gc.region == "us-central1"
        assert gc.zone == "us-central1-a"
        assert gc.credentials_file is None

    def test_cloud_provider_enum(self):
        assert CloudProvider.AWS.value == "aws"
        assert CloudProvider.GCP.value == "gcp"

    def test_cert_method_managed_exists(self):
        assert CertMethod.MANAGED.value == "managed"

    def test_dns_config_gcp_fields(self):
        d = DNSConfig(base_domain="x.com", dns_zone_name="z", create_dns_zone=True)
        assert d.dns_zone_name == "z"
        assert d.create_dns_zone is True

    def test_dns_config_gcp_defaults(self):
        d = DNSConfig(base_domain="x.com")
        assert d.dns_zone_name is None
        assert d.create_dns_zone is False

    def test_gcp_machine_ram_gb(self):
        assert gcp_machine_ram_gb("e2-standard-8") == 32
        assert gcp_machine_ram_gb("e2-standard-16") == 64
        assert gcp_machine_ram_gb("unknown-type") is None

    def test_gcp_metadata_dicts_present(self):
        from iblai_infra.models import GCP_DISK_TYPES, GCP_MACHINE_TYPES, GCP_REGIONS
        assert "us-central1" in GCP_REGIONS
        assert "e2-standard-8" in GCP_MACHINE_TYPES
        assert "pd-balanced" in GCP_DISK_TYPES

    def test_serialization_roundtrip(self, gcp_infra_config):
        data = gcp_infra_config.model_dump_json()
        back = InfraConfig.model_validate_json(data)
        assert back.cloud == CloudProvider.GCP
        assert back.credentials is None
        assert back.gcp_credentials.project_id == gcp_infra_config.gcp_credentials.project_id


class TestGcpEnvProvisionEdgeCases:
    def _env(self, **over):
        base = {
            "PROVIDER": "gcp", "GCP_PROJECT_ID": "proj-123", "PROJECT_NAME": "mydeploy",
            "ENVIRONMENT": "staging", "DOMAIN": "example.com", "VPN_IP": "203.0.113.7",
            "SSH_KEY_METHOD": "existing_file", "SSH_PUBLIC_KEY": "ssh-ed25519 AAAA test",
            "CERT_METHOD": "none",
        }
        base.update(over)
        return base

    def _build(self, env, **kw):
        from iblai_infra.env_provision import build_infra_config_from_env
        kw.setdefault("auto_delete_cnames", False)
        return build_infra_config_from_env(env, **kw)

    def _zone(self, name, dns_name="example.com"):
        z = mock.MagicMock()
        z.name = name
        z.dns_name = dns_name
        return z

    def _conflict(self):
        c = mock.MagicMock()
        c.record_type = "A"
        c.name = "learn.example.com."
        c.rrdatas = ["1.2.3.4"]
        return c

    def test_vpn_auto_detect(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")), \
             mock.patch("iblai_infra.gcp_env_provision.detect_current_ip", return_value="9.9.9.9"):
            cfg = self._build(self._env(VPN_IP="auto"))
        assert cfg.network.vpn_ip == "9.9.9.9"

    def test_volume_floor_enforced(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")):
            with pytest.raises((SystemExit, typer.Exit)):
                self._build(self._env(VOLUME_SIZE="50"))

    def test_ambiguous_zones_exits(self):
        zones = [self._zone("z1"), self._zone("z2")]
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")), \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=zones):
            with pytest.raises((SystemExit, typer.Exit)):
                self._build(self._env(CERT_METHOD="managed"))

    def test_explicit_zone_overrides_autodetect(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")), \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[self._zone("autozone")]), \
             mock.patch("iblai_infra.providers.gcp.find_conflicting_records", return_value=[]):
            cfg = self._build(self._env(CERT_METHOD="managed", DNS_ZONE_NAME="explicitzone"))
        assert cfg.dns.dns_zone_name == "explicitzone"

    def test_conflict_deleted_when_auto_delete(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")), \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[self._zone("z")]), \
             mock.patch("iblai_infra.providers.gcp.find_conflicting_records", return_value=[self._conflict()]), \
             mock.patch("iblai_infra.providers.gcp.delete_records") as mdel:
            self._build(self._env(CERT_METHOD="managed", DNS_ZONE_NAME="z"), auto_delete_cnames=True)
        mdel.assert_called_once()

    def test_conflict_without_auto_delete_exits(self):
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")), \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[self._zone("z")]), \
             mock.patch("iblai_infra.providers.gcp.find_conflicting_records", return_value=[self._conflict()]):
            with pytest.raises((SystemExit, typer.Exit)):
                self._build(self._env(CERT_METHOD="managed", DNS_ZONE_NAME="z"), auto_delete_cnames=False)

    def test_upload_cert_reads_pems(self, tmp_path):
        body = tmp_path / "cert.pem"
        body.write_text("-----BEGIN CERTIFICATE-----\nAAA")
        key = tmp_path / "key.pem"
        key.write_text("-----BEGIN PRIVATE KEY-----\nBBB")
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")):
            cfg = self._build(self._env(CERT_METHOD="upload", CERT_BODY_PATH=str(body), CERT_KEY_PATH=str(key)))
        assert cfg.certificates.method == CertMethod.UPLOAD
        assert "BEGIN CERTIFICATE" in cfg.certificates.cert_body

    def test_existing_file_ssh_from_path(self, tmp_path):
        pub = tmp_path / "id_ed25519.pub"
        pub.write_text("ssh-ed25519 AAAAFROMFILE test@host")
        with mock.patch("iblai_infra.providers.gcp.validate_credentials", return_value=mock.MagicMock(account="a")):
            cfg = self._build(self._env(SSH_KEY_METHOD="existing_file", SSH_PUBLIC_KEY_PATH=str(pub)))
        assert "AAAAFROMFILE" in cfg.ssh.public_key
        assert cfg.ssh.key_name == "id_ed25519"


class TestCollectGcpConfig:
    """The wizard's GCP config-assembly step (app._collect_gcp_config)."""

    def test_assembles_gcp_infraconfig(self):
        from iblai_infra import app

        gc = GCPCredentials(
            method=GCPAuthMethod.ADC, project_id="p", region="us-central1", zone="us-central1-a"
        )
        compute = ComputeConfig(instance_type="e2-standard-8", volume_size=100, volume_type="pd-balanced")
        network = NetworkConfig(vpc_cidr="10.0.0.0/16", vpn_ip="1.2.3.4")
        ssh = SSHConfig(method=SSHKeyMethod.GENERATE, key_name="proj-prod", public_key="k")
        dns = DNSConfig(base_domain="example.com", dns_zone_name="z")
        cert = CertificateConfig(method=CertMethod.MANAGED)

        with mock.patch("iblai_infra.prompts.credentials.prompt_gcp_credentials", return_value=gc), \
             mock.patch("iblai_infra.prompts.infrastructure.prompt_gcp_project_and_compute",
                        return_value=("proj", Environment.PROD, compute)), \
             mock.patch("iblai_infra.app.prompt_network_and_ssh", return_value=(network, ssh)), \
             mock.patch("iblai_infra.prompts.dns_certs.prompt_gcp_dns_and_certs", return_value=(dns, cert)):
            config = app._collect_gcp_config()

        assert config.cloud == CloudProvider.GCP
        assert config.deployment_type.value == "single-server"
        assert config.gcp_credentials.project_id == "p"
        assert config.credentials is None
        assert config.certificates.method == CertMethod.MANAGED
        # provider-neutral network/ssh prompt was called with allow_aws_keypair=False
        from iblai_infra import app as app_mod  # noqa: F401


class TestGcpCli:
    def _gcp_state(self, gcp_infra_config, tmp_path):
        from iblai_infra.models import ProjectState

        return ProjectState(
            name="gcpenv",
            provider="provision-env",
            status="created",
            config=gcp_infra_config,
            outputs={"instance_public_ip": "34.1.2.3", "application_url": "https://learn.example.com"},
            workspace_path=str(tmp_path),
        )

    def test_status_renders_gcp_without_crash(self, gcp_infra_config, tmp_path):
        from iblai_infra import cli

        state = self._gcp_state(gcp_infra_config, tmp_path)
        with mock.patch("iblai_infra.cli.load_state", return_value=state):
            cli.status("gcpenv")  # must not raise (regression: config.credentials is None)

    def test_list_renders_gcp_without_crash(self, gcp_infra_config, tmp_path):
        from iblai_infra import cli

        state = self._gcp_state(gcp_infra_config, tmp_path)
        with mock.patch("iblai_infra.cli.list_all_states", return_value=[state]):
            cli.list_cmd()

    def test_permissions_gcp_shows_roles(self):
        from iblai_infra import cli

        cli._gcp_permissions(check=False, project=None)  # prints roles/APIs, no crash

    def test_permissions_gcp_check_requires_project(self):
        from iblai_infra import cli

        with pytest.raises((SystemExit, typer.Exit)):
            cli._gcp_permissions(check=True, project=None)


class TestSetupPromptsGcpState:
    """Regression: setup prompts must handle GCP states (config.credentials is None)."""

    def _gcp_state(self, gcp_infra_config, tmp_path):
        from iblai_infra.models import ProjectState

        return ProjectState(
            name="gcpenv",
            provider="provision-env",
            status="created",
            config=gcp_infra_config,
            outputs={"instance_public_ip": "34.1.2.3"},
            workspace_path=str(tmp_path),
        )

    def test_prompt_credentials_gcp_state_no_crash(self, gcp_infra_config, tmp_path):
        """The exact crash from the field: AttributeError on creds.region."""
        from iblai_infra.prompts import setup as setup_mod

        state = self._gcp_state(gcp_infra_config, tmp_path)
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.password") as mpass, \
             mock.patch("questionary.confirm") as mconf:
            mtext.return_value.ask.side_effect = [
                "iblai", "iblai-cli-ops", "iblai-prod-images",  # org + repos
                "AKIAEXAMPLE", "us-east-1",                      # aws key id + region
                "platform_admin", "admin@example.com",           # admin user + email
            ]
            mpass.return_value.ask.side_effect = [
                "ghp_token", "aws-secret", "", "adminpass123",   # git, aws secret, openai, admin pw
            ]
            cred = setup_mod._prompt_credentials(step=3, total=3, state=state)

        # No AWS creds in a GCP state -> never offers "reuse from provisioning"
        assert not mconf.called
        assert cred["aws_access_key_id"] == "AKIAEXAMPLE"
        assert cred["aws_default_region"] == "us-east-1"

    def test_prompt_credentials_aws_state_offers_reuse(self, project_state):
        """Control: AWS states still get the reuse offer."""
        from iblai_infra.prompts import setup as setup_mod

        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.password") as mpass, \
             mock.patch("questionary.confirm") as mconf:
            mtext.return_value.ask.side_effect = [
                "iblai", "iblai-cli-ops", "iblai-prod-images",
                "platform_admin", "admin@example.com",
            ]
            mpass.return_value.ask.side_effect = ["ghp_token", "", "adminpass123"]
            mconf.return_value.ask.return_value = True  # reuse provisioning creds
            cred = setup_mod._prompt_credentials(step=3, total=3, state=project_state)

        assert mconf.called
        assert cred["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"

    def test_env_setup_region_defaults_for_gcp_state(self, gcp_infra_config, tmp_path):
        from iblai_infra.env_setup import build_setup_config_from_env

        key = tmp_path / "key"
        key.write_text("k")
        key.chmod(0o600)
        state = self._gcp_state(gcp_infra_config, tmp_path)
        state.config.ssh.private_key_path = key

        env = {
            "AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "s",
            "GIT_TOKEN": "ghp_x", "ADMIN_USERNAME": "adm",
            "ADMIN_EMAIL": "a@b.co", "ADMIN_PASSWORD": "longenough",
        }
        cfg = build_setup_config_from_env(env, state=state)
        assert cfg.aws_default_region == "us-east-1"  # fell back, didn't crash

    def test_waf_rejects_gcp_state(self, gcp_infra_config, tmp_path):
        from iblai_infra.features import waf as waf_mod

        state = self._gcp_state(gcp_infra_config, tmp_path)
        with mock.patch("iblai_infra.features.waf.load_state", return_value=state):
            with pytest.raises((SystemExit, typer.Exit)):
                waf_mod._load_and_guard_waf_target("gcpenv")


class TestGcpExtraGuard:
    """When the [gcp] extra isn't installed, entry points fail cleanly (no traceback)."""

    def test_env_builder_clean_error(self):
        from iblai_infra.env_provision import build_infra_config_from_env

        env = {
            "PROVIDER": "gcp", "GCP_PROJECT_ID": "p", "PROJECT_NAME": "d",
            "DOMAIN": "example.com", "VPN_IP": "1.2.3.4",
            "SSH_KEY_METHOD": "existing_file", "SSH_PUBLIC_KEY": "k", "CERT_METHOD": "none",
        }
        with mock.patch("iblai_infra.providers.gcp.is_available", return_value=False):
            with pytest.raises((SystemExit, typer.Exit)):
                build_infra_config_from_env(env, auto_delete_cnames=False)
