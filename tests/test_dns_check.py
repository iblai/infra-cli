"""Tests for DNS + certificate verification."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from iblai_infra.dns_check import (
    DNSReport,
    RecordResult,
    RecordStatus,
    certificate_status,
    check_dns,
    expected_target,
)
from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CertificateConfig,
    CertMethod,
    CloudProvider,
    ComputeConfig,
    DeploymentType,
    DNSConfig,
    GCPCredentials,
    InfraConfig,
    NetworkConfig,
    SSHConfig,
    SSHKeyMethod,
)


def _config(**kwargs) -> InfraConfig:
    defaults = dict(
        project_name="test",
        environment="prod",
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY, region="us-east-1",
            access_key_id="AK", secret_access_key="SK",
        ),
        network=NetworkConfig(vpn_ip="1.2.3.4"),
        compute=ComputeConfig(),
        ssh=SSHConfig(method=SSHKeyMethod.GENERATE, key_name="k"),
        certificates=CertificateConfig(method=CertMethod.NONE),
        dns=DNSConfig(base_domain="example.com"),
    )
    defaults.update(kwargs)
    return InfraConfig(**defaults)


def _gcp_config(**kwargs) -> InfraConfig:
    kwargs.setdefault("cloud", CloudProvider.GCP)
    kwargs.setdefault("credentials", None)
    kwargs.setdefault("gcp_credentials", GCPCredentials(project_id="proj"))
    return _config(**kwargs)


# ---------------------------------------------------------------------------
# expected_target
# ---------------------------------------------------------------------------


class TestExpectedTarget:
    def test_aws_uses_alb_dns_name(self):
        assert expected_target(_config(), {"alb_dns_name": "alb.example.com"}) == "alb.example.com"

    def test_gcp_uses_lb_ip(self):
        assert expected_target(_gcp_config(), {"lb_ip_address": "34.1.2.3"}) == "34.1.2.3"

    def test_call_server_uses_elastic_ip(self):
        cfg = _config(deployment_type=DeploymentType.CALL)
        assert expected_target(cfg, {"elastic_ip": "5.6.7.8"}) == "5.6.7.8"

    def test_missing_output_is_empty(self):
        assert expected_target(_config(), {}) == ""


# ---------------------------------------------------------------------------
# check_dns
# ---------------------------------------------------------------------------


class TestCheckDNS:
    def test_all_records_pointing_at_target_are_ok(self):
        cfg = _gcp_config()
        with patch("iblai_infra.dns_check.resolve_addresses", return_value=["34.1.2.3"]):
            report = check_dns(cfg, {"lb_ip_address": "34.1.2.3"})
        assert report.all_ok
        assert not report.problems
        assert len(report.records) == len(cfg.dns.subdomains)

    def test_unresolvable_records_are_missing(self):
        cfg = _gcp_config()
        with patch("iblai_infra.dns_check.resolve_addresses", return_value=[]):
            report = check_dns(cfg, {"lb_ip_address": "34.1.2.3"})
        assert not report.all_ok
        assert all(r.status is RecordStatus.MISSING for r in report.records)

    def test_records_pointing_elsewhere_are_wrong(self):
        """The dangerous case: the name resolves, just not to us."""
        cfg = _gcp_config()

        def fake(name):
            return ["34.1.2.3"] if name == "34.1.2.3" else ["9.9.9.9"]

        with patch("iblai_infra.dns_check.resolve_addresses", side_effect=fake):
            report = check_dns(cfg, {"lb_ip_address": "34.1.2.3"})
        assert all(r.status is RecordStatus.WRONG for r in report.records)
        assert report.records[0].resolved == ["9.9.9.9"]

    def test_aws_hostname_target_is_resolved_for_comparison(self):
        cfg = _config()
        calls = []

        def fake(name):
            calls.append(name)
            return ["1.1.1.1"]

        with patch("iblai_infra.dns_check.resolve_addresses", side_effect=fake):
            report = check_dns(cfg, {"alb_dns_name": "alb.example.com"})
        assert "alb.example.com" in calls  # the ALB itself was resolved
        assert report.target_ips == ["1.1.1.1"]
        assert report.all_ok

    def test_unresolvable_target_does_not_produce_false_wrong(self):
        """If the LB itself can't be resolved we can only say the name resolves."""
        cfg = _config()

        def fake(name):
            return [] if name == "alb.example.com" else ["9.9.9.9"]

        with patch("iblai_infra.dns_check.resolve_addresses", side_effect=fake):
            report = check_dns(cfg, {"alb_dns_name": "alb.example.com"})
        assert all(r.status is RecordStatus.OK for r in report.records)

    def test_call_server_checks_only_the_base_domain(self):
        cfg = _config(deployment_type=DeploymentType.CALL,
                      dns=DNSConfig(base_domain="call.example.com"))
        with patch("iblai_infra.dns_check.resolve_addresses", return_value=["5.6.7.8"]):
            report = check_dns(cfg, {"elastic_ip": "5.6.7.8"})
        assert [r.name for r in report.records] == ["call.example.com"]

    def test_mixed_results_summarise(self):
        cfg = _gcp_config()
        seq = {"learn.example.com": ["34.1.2.3"]}

        def fake(name):
            if name == "34.1.2.3":
                return ["34.1.2.3"]
            return seq.get(name, [])

        with patch("iblai_infra.dns_check.resolve_addresses", side_effect=fake):
            report = check_dns(cfg, {"lb_ip_address": "34.1.2.3"})
        assert len(report.ok) == 1
        assert "1/" in report.summary()


# ---------------------------------------------------------------------------
# certificate_status
# ---------------------------------------------------------------------------


class TestCertificateStatus:
    def test_none_and_upload_have_no_status(self):
        assert certificate_status(_config(certificates=CertificateConfig(method=CertMethod.NONE)), {}) is None
        assert certificate_status(_config(certificates=CertificateConfig(method=CertMethod.UPLOAD)), {}) is None

    def test_acm_reports_the_blocking_state(self):
        cfg = _config(certificates=CertificateConfig(method=CertMethod.ACM, hosted_zone_id="Z"))
        outputs = {"certificate_arn_1": "arn:1", "certificate_arn_2": "arn:2"}

        class FakeACM:
            def __init__(self):
                self.seen = []

            def describe_certificate(self, CertificateArn):
                self.seen.append(CertificateArn)
                status = "ISSUED" if CertificateArn == "arn:1" else "PENDING_VALIDATION"
                return {"Certificate": {"Status": status}}

        fake = FakeACM()
        with patch("iblai_infra.providers.aws.get_session") as sess:
            sess.return_value.client.return_value = fake
            assert certificate_status(cfg, outputs) == "PENDING_VALIDATION"

    def test_acm_all_issued(self):
        cfg = _config(certificates=CertificateConfig(method=CertMethod.ACM, hosted_zone_id="Z"))
        with patch("iblai_infra.providers.aws.get_session") as sess:
            sess.return_value.client.return_value.describe_certificate.return_value = {
                "Certificate": {"Status": "ISSUED"}
            }
            assert certificate_status(cfg, {"certificate_arn_1": "arn:1"}) == "ISSUED"

    def test_failure_is_swallowed(self):
        """A diagnostic must never break the check."""
        cfg = _config(certificates=CertificateConfig(method=CertMethod.ACM, hosted_zone_id="Z"))
        with patch("iblai_infra.providers.aws.get_session", side_effect=RuntimeError("boom")):
            assert certificate_status(cfg, {"certificate_arn_1": "arn:1"}) is None


# ---------------------------------------------------------------------------
# report helpers
# ---------------------------------------------------------------------------


class TestReport:
    def test_empty_report_is_not_all_ok(self):
        assert DNSReport(records=[], target="", target_ips=[]).all_ok is False

    def test_partitions(self):
        report = DNSReport(
            records=[
                RecordResult("a", RecordStatus.OK, ["1.1.1.1"]),
                RecordResult("b", RecordStatus.MISSING),
                RecordResult("c", RecordStatus.WRONG, ["9.9.9.9"]),
            ],
            target="1.1.1.1",
            target_ips=["1.1.1.1"],
        )
        assert len(report.ok) == 1
        assert len(report.problems) == 2
        assert report.summary() == "1/3 resolving to the load balancer"


# ---------------------------------------------------------------------------
# resolve_addresses error handling
# ---------------------------------------------------------------------------


class TestResolveAddresses:
    def test_resolver_failure_returns_empty(self):
        from iblai_infra import dns_check

        with patch.object(dns_check, "_resolver", side_effect=RuntimeError("no network")):
            assert dns_check.resolve_addresses("example.com") == []
