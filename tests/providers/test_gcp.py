"""Tests for the GCP provider helpers (mocked Google SDK)."""

from __future__ import annotations

from unittest import mock

import pytest

from iblai_infra.models import GCPAuthMethod, GCPCredentials
from iblai_infra.providers import gcp


@pytest.fixture
def gc() -> GCPCredentials:
    return GCPCredentials(
        method=GCPAuthMethod.SERVICE_ACCOUNT_KEY,
        project_id="proj-123",
        region="us-central1",
        zone="us-central1-a",
        credentials_file="/tmp/key.json",
    )


class TestRequireGcp:
    def test_raises_when_extra_missing(self, monkeypatch):
        monkeypatch.setattr(gcp, "_GCP_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="iblai-infra\\[gcp\\]"):
            gcp._require_gcp()

    def test_noop_when_available(self, monkeypatch):
        monkeypatch.setattr(gcp, "_GCP_AVAILABLE", True)
        gcp._require_gcp()  # should not raise


class TestValidateCredentials:
    def test_success_service_account(self, gc):
        creds = mock.MagicMock()
        creds.valid = True
        creds.service_account_email = "sa@proj-123.iam.gserviceaccount.com"
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(creds, "proj-123")):
            identity = gcp.validate_credentials(gc)
        assert identity.project_id == "proj-123"
        assert identity.account == "sa@proj-123.iam.gserviceaccount.com"

    def test_refreshes_when_not_valid(self, gc):
        creds = mock.MagicMock()
        creds.valid = False
        creds.service_account_email = "sa@proj-123.iam.gserviceaccount.com"
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(creds, "proj-123")):
            gcp.validate_credentials(gc)
        creds.refresh.assert_called_once()

    def test_failure_raises_valueerror(self, gc):
        with mock.patch.object(gcp, "_scoped_credentials", side_effect=ValueError("no ADC")):
            with pytest.raises(ValueError, match="no ADC"):
                gcp.validate_credentials(gc)

    def test_unexpected_error_wrapped_as_valueerror(self, gc):
        with mock.patch.object(gcp, "_scoped_credentials", side_effect=RuntimeError("boom")):
            with pytest.raises(ValueError, match="GCP authentication failed"):
                gcp.validate_credentials(gc)


class TestListManagedZones:
    def _zone(self, name, dns_name, visibility="public"):
        z = mock.MagicMock()
        z.name = name
        z.dns_name = dns_name
        z.visibility = visibility
        return z

    def test_lists_public_zones_and_strips_dot(self, gc):
        client = mock.MagicMock()
        client.list_zones.return_value = [
            self._zone("z1", "example.com."),
            self._zone("z2", "sub.example.org."),
        ]
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(mock.MagicMock(), "proj-123")):
            with mock.patch.object(gcp.dns, "Client", return_value=client):
                zones = gcp.list_managed_zones(gc)
        assert [(z.name, z.dns_name) for z in zones] == [
            ("z1", "example.com"),
            ("z2", "sub.example.org"),
        ]

    def test_filters_private_zones(self, gc):
        client = mock.MagicMock()
        client.list_zones.return_value = [
            self._zone("pub", "example.com.", "public"),
            self._zone("priv", "internal.example.com.", "private"),
        ]
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(mock.MagicMock(), "proj-123")):
            with mock.patch.object(gcp.dns, "Client", return_value=client):
                zones = gcp.list_managed_zones(gc)
        assert [z.name for z in zones] == ["pub"]

    def test_returns_empty_on_error(self, gc):
        with mock.patch.object(gcp, "_scoped_credentials", side_effect=RuntimeError("denied")):
            assert gcp.list_managed_zones(gc) == []


class TestFindConflictingRecords:
    def _rrs(self, name, rtype):
        r = mock.MagicMock()
        r.name = name
        r.record_type = rtype
        r.rrdatas = ["1.2.3.4"]
        return r

    def test_finds_a_and_cname_at_target_names(self, gc):
        zone = mock.MagicMock()
        zone.list_resource_record_sets.return_value = [
            self._rrs("learn.example.com.", "A"),       # conflict
            self._rrs("api.example.com.", "CNAME"),      # conflict
            self._rrs("learn.example.com.", "TXT"),      # not A/CNAME -> ignore
            self._rrs("other.example.com.", "A"),        # not a target -> ignore
        ]
        client = mock.MagicMock()
        client.zone.return_value = zone
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(mock.MagicMock(), "proj-123")):
            with mock.patch.object(gcp.dns, "Client", return_value=client):
                conflicts = gcp.find_conflicting_records(
                    gc, "example-zone", ["learn.example.com", "api.example.com"]
                )
        assert {(c.name, c.record_type) for c in conflicts} == {
            ("learn.example.com.", "A"),
            ("api.example.com.", "CNAME"),
        }


class TestCheckPermissions:
    def _resp(self, status, text=""):
        r = mock.MagicMock()
        r.status_code = status
        r.text = text
        r.json.return_value = {"error": {"message": text}} if text else {}
        return r

    def test_all_pass(self, gc):
        session = mock.MagicMock()
        session.get.return_value = self._resp(200)
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(mock.MagicMock(), "proj-123")):
            with mock.patch.object(gcp, "AuthorizedSession", return_value=session):
                results = gcp.check_permissions(gc)
        assert len(results) == 2
        assert all(r.passed for r in results)

    def test_service_disabled_reported(self, gc):
        session = mock.MagicMock()
        session.get.return_value = self._resp(403, "SERVICE_DISABLED: compute has not been used")
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(mock.MagicMock(), "proj-123")):
            with mock.patch.object(gcp, "AuthorizedSession", return_value=session):
                results = gcp.check_permissions(gc)
        assert all(not r.passed for r in results)
        assert all(r.error == "API not enabled for this project" for r in results)

    def test_credentials_failure(self, gc):
        with mock.patch.object(gcp, "_scoped_credentials", side_effect=ValueError("bad creds")):
            results = gcp.check_permissions(gc)
        assert len(results) == 1
        assert results[0].service == "Credentials"
        assert not results[0].passed


class TestRequiredRoles:
    def test_shape(self):
        assert "roles" in gcp.REQUIRED_GCP_ROLES
        assert "apis" in gcp.REQUIRED_GCP_ROLES
        assert "roles/compute.admin" in gcp.REQUIRED_GCP_ROLES["roles"]
        assert "compute.googleapis.com" in gcp.REQUIRED_GCP_ROLES["apis"]


class TestScopedCredentials:
    """Credential resolution — the GCP analog of aws.get_session."""

    def _adc_gc(self, project_id="proj-123"):
        return GCPCredentials(method=GCPAuthMethod.ADC, project_id=project_id)

    def test_service_account_key_loads_file(self, gc):
        fake = mock.MagicMock()
        fake.with_quota_project.return_value = "quota-scoped"
        with mock.patch.object(gcp, "service_account") as msa:
            msa.Credentials.from_service_account_file.return_value = fake
            creds, project = gcp._scoped_credentials(gc)
        msa.Credentials.from_service_account_file.assert_called_once()
        assert project == "proj-123"
        # user/SA creds pinned to a quota project
        fake.with_quota_project.assert_called_once_with("proj-123")
        assert creds == "quota-scoped"

    def test_service_account_key_missing_file_raises(self):
        bad = GCPCredentials(
            method=GCPAuthMethod.SERVICE_ACCOUNT_KEY, project_id="p", credentials_file=None
        )
        with pytest.raises(ValueError, match="credentials_file"):
            gcp._scoped_credentials(bad)

    def test_adc_uses_explicit_project(self):
        fake = mock.MagicMock()
        with mock.patch("iblai_infra.providers.gcp.google.auth.default",
                        return_value=(fake, "adc-proj")) as md:
            _, project = gcp._scoped_credentials(self._adc_gc("explicit"))
        md.assert_called_once()
        assert project == "explicit"  # explicit project_id wins over the ADC project

    def test_adc_falls_back_to_adc_project(self):
        fake = mock.MagicMock()
        with mock.patch("iblai_infra.providers.gcp.google.auth.default",
                        return_value=(fake, "adc-proj")):
            _, project = gcp._scoped_credentials(self._adc_gc(project_id=""))
        assert project == "adc-proj"

    def test_adc_failure_gives_friendly_error(self):
        with mock.patch("iblai_infra.providers.gcp.google.auth.default",
                        side_effect=Exception("no ADC file")):
            with pytest.raises(ValueError, match="Application Default Credentials"):
                gcp._scoped_credentials(self._adc_gc())


class TestIdentityUserinfo:
    def test_adc_identity_via_userinfo(self):
        gc = GCPCredentials(method=GCPAuthMethod.ADC, project_id="p")
        creds = mock.MagicMock()
        creds.valid = True
        creds.service_account_email = None
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"email": "user@example.com"}
        session = mock.MagicMock()
        session.get.return_value = resp
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(creds, "p")), \
             mock.patch.object(gcp, "AuthorizedSession", return_value=session):
            identity = gcp.validate_credentials(gc)
        assert identity.account == "user@example.com"


class TestDeleteRecords:
    def _gc(self):
        return GCPCredentials(
            method=GCPAuthMethod.SERVICE_ACCOUNT_KEY, project_id="p", credentials_file="/tmp/k.json"
        )

    def test_submits_deletions(self):
        changes = mock.MagicMock()
        zone = mock.MagicMock()
        zone.changes.return_value = changes
        client = mock.MagicMock()
        client.zone.return_value = zone
        records = [mock.MagicMock(), mock.MagicMock()]
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(mock.MagicMock(), "p")):
            with mock.patch.object(gcp.dns, "Client", return_value=client):
                gcp.delete_records(self._gc(), "zone", records)
        assert changes.delete_record_set.call_count == 2
        changes.create.assert_called_once()

    def test_noop_on_empty_records(self):
        with mock.patch.object(gcp, "_scoped_credentials") as ms:
            gcp.delete_records(self._gc(), "zone", [])
        ms.assert_not_called()


class TestCheckPermissionsMixed:
    def _resp(self, status, text=""):
        r = mock.MagicMock()
        r.status_code = status
        r.text = text
        r.json.return_value = {"error": {"message": text}} if text else {}
        return r

    def test_one_pass_one_denied(self):
        gc = GCPCredentials(method=GCPAuthMethod.ADC, project_id="p")
        session = mock.MagicMock()
        session.get.side_effect = [self._resp(200), self._resp(403, "permission denied")]
        with mock.patch.object(gcp, "_scoped_credentials", return_value=(mock.MagicMock(), "p")):
            with mock.patch.object(gcp, "AuthorizedSession", return_value=session):
                results = gcp.check_permissions(gc)
        assert results[0].passed is True
        assert results[1].passed is False
        assert results[1].error == "permission denied"


class TestIsAvailable:
    def test_reflects_flag(self, monkeypatch):
        monkeypatch.setattr(gcp, "_GCP_AVAILABLE", True)
        assert gcp.is_available() is True
        monkeypatch.setattr(gcp, "_GCP_AVAILABLE", False)
        assert gcp.is_available() is False
