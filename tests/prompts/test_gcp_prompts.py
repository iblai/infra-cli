"""Tests for the GCP wizard prompts (questionary mocked)."""

from __future__ import annotations

from unittest import mock

import iblai_infra.prompts.credentials as creds_mod
import iblai_infra.prompts.dns_certs as dns_mod
import iblai_infra.prompts.infrastructure as infra_mod
from iblai_infra.models import (
    CertMethod,
    CloudProvider,
    Environment,
    GCP_MACHINE_TYPES,
    GCPAuthMethod,
    GCPCredentials,
)


class TestPromptProvider:
    def test_returns_gcp(self):
        with mock.patch("questionary.select") as msel:
            msel.return_value.ask.return_value = CloudProvider.GCP
            assert creds_mod.prompt_provider() == CloudProvider.GCP

    def test_returns_aws(self):
        with mock.patch("questionary.select") as msel:
            msel.return_value.ask.return_value = CloudProvider.AWS
            assert creds_mod.prompt_provider() == CloudProvider.AWS


class TestPromptGcpCredentials:
    def test_adc(self):
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.autocomplete") as mauto, \
             mock.patch("questionary.select") as msel, \
             mock.patch("iblai_infra.providers.gcp.validate_credentials") as mval:
            mtext.return_value.ask.side_effect = ["my-project", "us-central1-a"]  # project_id, zone
            mauto.return_value.ask.return_value = "us-central1"                    # region
            msel.return_value.ask.return_value = GCPAuthMethod.ADC                 # auth method
            mval.return_value = mock.MagicMock(account="user@example.com")
            creds = creds_mod.prompt_gcp_credentials(show_step=False)
        assert creds.project_id == "my-project"
        assert creds.region == "us-central1"
        assert creds.zone == "us-central1-a"
        assert creds.method == GCPAuthMethod.ADC
        assert creds.account == "user@example.com"
        assert creds.credentials_file is None

    def test_service_account_key(self, tmp_path):
        keyf = tmp_path / "key.json"
        keyf.write_text("{}")
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.autocomplete") as mauto, \
             mock.patch("questionary.select") as msel, \
             mock.patch("questionary.path") as mpath, \
             mock.patch("iblai_infra.providers.gcp.validate_credentials",
                        return_value=mock.MagicMock(account="sa@x")):
            mtext.return_value.ask.side_effect = ["proj", "us-central1-a"]
            mauto.return_value.ask.return_value = "us-central1"
            msel.return_value.ask.return_value = GCPAuthMethod.SERVICE_ACCOUNT_KEY
            mpath.return_value.ask.return_value = str(keyf)
            creds = creds_mod.prompt_gcp_credentials(show_step=False)
        assert creds.method == GCPAuthMethod.SERVICE_ACCOUNT_KEY
        assert creds.credentials_file == str(keyf)

    def test_invalid_credentials_aborts(self):
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.autocomplete") as mauto, \
             mock.patch("questionary.select") as msel, \
             mock.patch("iblai_infra.providers.gcp.validate_credentials",
                        side_effect=ValueError("bad")):
            mtext.return_value.ask.side_effect = ["p", "us-central1-a"]
            mauto.return_value.ask.return_value = "us-central1"
            msel.return_value.ask.return_value = GCPAuthMethod.ADC
            with mock.patch("iblai_infra.ui.abort", side_effect=SystemExit) as mabort:
                try:
                    creds_mod.prompt_gcp_credentials(show_step=False)
                except SystemExit:
                    pass
        assert mabort.called


class TestPromptGcpProjectAndCompute:
    def test_returns_project_env_compute(self):
        label = f"e2-standard-8  — {GCP_MACHINE_TYPES['e2-standard-8']}"
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.select") as msel, \
             mock.patch("questionary.autocomplete") as mauto:
            mtext.return_value.ask.side_effect = ["MyProj", "100"]        # name, volume size
            msel.return_value.ask.side_effect = [Environment.PROD, "pd-balanced"]  # env, disk type
            mauto.return_value.ask.return_value = label                  # machine type
            name, env, compute = infra_mod.prompt_gcp_project_and_compute()
        assert name == "myproj"
        assert env == Environment.PROD
        assert compute.instance_type == "e2-standard-8"
        assert compute.volume_type == "pd-balanced"
        assert compute.volume_size == 100


class TestPromptGcpDnsAndCerts:
    def _gc(self):
        return GCPCredentials(method=GCPAuthMethod.ADC, project_id="p")

    def _zone(self, name="myzone", dns_name="example.com"):
        z = mock.MagicMock()
        z.name = name
        z.dns_name = dns_name
        return z

    def test_managed_existing_zone(self):
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.select") as msel, \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[self._zone()]), \
             mock.patch("iblai_infra.providers.gcp.find_conflicting_records", return_value=[]):
            mtext.return_value.ask.return_value = "app.example.com"
            msel.return_value.ask.return_value = "managed"
            dns, cert = dns_mod.prompt_gcp_dns_and_certs(self._gc())
        assert cert.method == CertMethod.MANAGED
        assert dns.dns_zone_name == "myzone"
        assert dns.create_dns_zone is False

    def test_create_zone(self):
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.select") as msel, \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[]):
            mtext.return_value.ask.side_effect = ["new.example.org", "newzone"]
            msel.return_value.ask.return_value = "create"
            dns, cert = dns_mod.prompt_gcp_dns_and_certs(self._gc())
        assert cert.method == CertMethod.MANAGED
        assert dns.create_dns_zone is True
        assert dns.dns_zone_name == "newzone"

    def test_none(self):
        with mock.patch("questionary.text") as mtext, \
             mock.patch("questionary.select") as msel, \
             mock.patch("questionary.confirm") as mconf, \
             mock.patch("iblai_infra.providers.gcp.list_managed_zones", return_value=[self._zone()]):
            mtext.return_value.ask.return_value = "x.example.com"
            msel.return_value.ask.return_value = "none"
            mconf.return_value.ask.return_value = True
            dns, cert = dns_mod.prompt_gcp_dns_and_certs(self._gc())
        assert cert.method == CertMethod.NONE
