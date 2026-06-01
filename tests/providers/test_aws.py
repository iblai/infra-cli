"""Tests for iblai_infra.providers.aws — boto3 wrappers and credential handling."""

from __future__ import annotations

import os
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

from iblai_infra.models import AWSCredentials, AuthMethod
from iblai_infra.providers.aws import (
    CallerIdentity,
    HostedZone,
    KeyPairInfo,
    PermissionCheckResult,
    check_bucket_exists,
    check_permissions,
    detect_current_ip,
    get_session,
    has_env_credentials,
    list_hosted_zones,
    list_key_pairs,
    list_profiles,
    validate_credentials,
)


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


class TestGetSession:
    def test_profile_session(self):
        creds = AWSCredentials(method=AuthMethod.PROFILE, profile="myprofile", region="us-east-1")
        with patch("iblai_infra.providers.aws.boto3.Session") as mock_session:
            get_session(creds)
            mock_session.assert_called_once_with(profile_name="myprofile", region_name="us-east-1")

    def test_access_key_session(self):
        creds = AWSCredentials(
            method=AuthMethod.ACCESS_KEY,
            access_key_id="AKIA",
            secret_access_key="secret",
            region="eu-west-1",
        )
        with patch("iblai_infra.providers.aws.boto3.Session") as mock_session:
            get_session(creds)
            mock_session.assert_called_once_with(
                aws_access_key_id="AKIA",
                aws_secret_access_key="secret",
                region_name="eu-west-1",
            )

    def test_environment_session(self):
        creds = AWSCredentials(method=AuthMethod.ENVIRONMENT, region="ap-south-1")
        with patch("iblai_infra.providers.aws.boto3.Session") as mock_session:
            get_session(creds)
            mock_session.assert_called_once_with(region_name="ap-south-1")


# ---------------------------------------------------------------------------
# has_env_credentials
# ---------------------------------------------------------------------------


class TestHasEnvCredentials:
    def test_both_set(self):
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK"}):
            assert has_env_credentials() is True

    def test_missing_key_id(self):
        env = {"AWS_SECRET_ACCESS_KEY": "SK"}
        with patch.dict(os.environ, env, clear=True):
            assert has_env_credentials() is False

    def test_missing_secret(self):
        env = {"AWS_ACCESS_KEY_ID": "AK"}
        with patch.dict(os.environ, env, clear=True):
            assert has_env_credentials() is False

    def test_both_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            assert has_env_credentials() is False

    def test_empty_values(self):
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""}):
            assert has_env_credentials() is False


# ---------------------------------------------------------------------------
# validate_credentials
# ---------------------------------------------------------------------------


class TestValidateCredentials:
    def test_success(self):
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test",
            "UserId": "AIDAEXAMPLE",
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_sts

        with patch("iblai_infra.providers.aws.get_session", return_value=mock_session):
            creds = AWSCredentials(method=AuthMethod.ENVIRONMENT, region="us-east-1")
            identity = validate_credentials(creds)
            assert identity.account_id == "123456789012"
            assert identity.user_id == "AIDAEXAMPLE"

    def test_profile_not_found(self):
        with patch("iblai_infra.providers.aws.get_session", side_effect=ProfileNotFound(profile="bad")):
            creds = AWSCredentials(method=AuthMethod.PROFILE, profile="bad", region="us-east-1")
            with pytest.raises(ValueError, match="not found"):
                validate_credentials(creds)

    def test_no_credentials(self):
        mock_session = MagicMock()
        mock_session.client.return_value.get_caller_identity.side_effect = NoCredentialsError()

        with patch("iblai_infra.providers.aws.get_session", return_value=mock_session):
            creds = AWSCredentials(method=AuthMethod.ENVIRONMENT, region="us-east-1")
            with pytest.raises(ValueError, match="No AWS credentials"):
                validate_credentials(creds)

    def test_client_error(self):
        error_response = {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}
        mock_session = MagicMock()
        mock_session.client.return_value.get_caller_identity.side_effect = ClientError(
            error_response, "GetCallerIdentity"
        )

        with patch("iblai_infra.providers.aws.get_session", return_value=mock_session):
            creds = AWSCredentials(method=AuthMethod.ENVIRONMENT, region="us-east-1")
            with pytest.raises(ValueError, match="authentication failed"):
                validate_credentials(creds)


# ---------------------------------------------------------------------------
# list_hosted_zones
# ---------------------------------------------------------------------------


class TestListHostedZones:
    def test_returns_public_zones(self):
        mock_r53 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "HostedZones": [
                    {
                        "Id": "/hostedzone/Z12345",
                        "Name": "example.com.",
                        "ResourceRecordSetCount": 10,
                        "Config": {"PrivateZone": False},
                    },
                    {
                        "Id": "/hostedzone/Z67890",
                        "Name": "internal.example.com.",
                        "ResourceRecordSetCount": 5,
                        "Config": {"PrivateZone": True},
                    },
                ]
            }
        ]
        mock_r53.get_paginator.return_value = paginator

        session = MagicMock()
        session.client.return_value = mock_r53

        zones = list_hosted_zones(session)
        assert len(zones) == 1
        assert zones[0].zone_id == "Z12345"
        assert zones[0].name == "example.com"
        assert zones[0].private is False

    def test_strips_trailing_dot(self):
        mock_r53 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "HostedZones": [
                    {
                        "Id": "/hostedzone/Z111",
                        "Name": "my.domain.org.",
                        "ResourceRecordSetCount": 3,
                        "Config": {},
                    },
                ]
            }
        ]
        mock_r53.get_paginator.return_value = paginator
        session = MagicMock()
        session.client.return_value = mock_r53

        zones = list_hosted_zones(session)
        assert zones[0].name == "my.domain.org"

    def test_returns_empty_on_error(self):
        session = MagicMock()
        session.client.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}}, "ListHostedZones"
        )
        assert list_hosted_zones(session) == []


# ---------------------------------------------------------------------------
# list_key_pairs
# ---------------------------------------------------------------------------


class TestListKeyPairs:
    def test_returns_key_pairs(self):
        session = MagicMock()
        session.client.return_value.describe_key_pairs.return_value = {
            "KeyPairs": [
                {"KeyName": "my-key", "KeyPairId": "key-123", "KeyType": "ed25519"},
                {"KeyName": "old-key", "KeyPairId": "key-456", "KeyType": "rsa"},
            ]
        }
        keys = list_key_pairs(session)
        assert len(keys) == 2
        assert keys[0].name == "my-key"
        assert keys[0].key_type == "ed25519"

    def test_returns_empty_on_error(self):
        session = MagicMock()
        session.client.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}}, "DescribeKeyPairs"
        )
        assert list_key_pairs(session) == []


# ---------------------------------------------------------------------------
# check_bucket_exists
# ---------------------------------------------------------------------------


class TestCheckBucketExists:
    def test_bucket_exists_owned(self):
        session = MagicMock()
        session.client.return_value.head_bucket.return_value = {}
        assert check_bucket_exists(session, "my-bucket") is True

    def test_bucket_exists_owned_by_other(self):
        session = MagicMock()
        session.client.return_value.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403"}}, "HeadBucket"
        )
        assert check_bucket_exists(session, "taken-bucket") is True

    def test_bucket_does_not_exist(self):
        session = MagicMock()
        session.client.return_value.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "HeadBucket"
        )
        assert check_bucket_exists(session, "new-bucket") is False

    def test_returns_false_on_botocore_error(self):
        from botocore.exceptions import BotoCoreError

        session = MagicMock()
        session.client.return_value.head_bucket.side_effect = BotoCoreError()
        assert check_bucket_exists(session, "bucket") is False


# ---------------------------------------------------------------------------
# detect_current_ip
# ---------------------------------------------------------------------------


class TestDetectCurrentIP:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"203.0.113.42\n"
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            ip = detect_current_ip()
            assert ip == "203.0.113.42"

    def test_returns_none_on_failure(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            assert detect_current_ip() is None


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------


class TestListProfiles:
    def test_reads_config_file(self, tmp_path):
        config = tmp_path / ".aws" / "config"
        config.parent.mkdir(parents=True)
        config.write_text("[profile myprofile]\nregion = us-east-1\n")

        with patch("iblai_infra.providers.aws.Path.home", return_value=tmp_path):
            profiles = list_profiles()
            assert "myprofile" in profiles

    def test_reads_credentials_file(self, tmp_path):
        creds = tmp_path / ".aws" / "credentials"
        creds.parent.mkdir(parents=True)
        creds.write_text("[myprofile]\naws_access_key_id = AK\n")

        with patch("iblai_infra.providers.aws.Path.home", return_value=tmp_path):
            profiles = list_profiles()
            assert "myprofile" in profiles

    def test_deduplicates_profiles(self, tmp_path):
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir(parents=True)
        (aws_dir / "config").write_text("[profile shared]\nregion = us-east-1\n")
        (aws_dir / "credentials").write_text("[shared]\naws_access_key_id = AK\n")

        with patch("iblai_infra.providers.aws.Path.home", return_value=tmp_path):
            profiles = list_profiles()
            assert profiles.count("shared") == 1

    def test_no_aws_directory(self, tmp_path):
        with patch("iblai_infra.providers.aws.Path.home", return_value=tmp_path):
            profiles = list_profiles()
            assert profiles == []

    def test_sorted_output(self, tmp_path):
        creds = tmp_path / ".aws" / "credentials"
        creds.parent.mkdir(parents=True)
        creds.write_text("[zeta]\n[alpha]\n[beta]\n")

        with patch("iblai_infra.providers.aws.Path.home", return_value=tmp_path):
            profiles = list_profiles()
            assert profiles == sorted(profiles)


# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------


class TestCheckPermissions:
    def test_all_pass(self):
        session = MagicMock()
        # EC2 dry-run returns DryRunOperation (success)
        ec2_error = ClientError(
            {"Error": {"Code": "DryRunOperation", "Message": ""}}, "DescribeVpcs"
        )
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.side_effect = ec2_error

        def client_factory(service):
            if service == "ec2":
                return ec2_client
            return MagicMock()

        session.client.side_effect = client_factory
        results = check_permissions(session)
        assert all(r.passed for r in results)
        assert len(results) == 8

    def test_ec2_denied(self):
        session = MagicMock()
        ec2_error = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}}, "DescribeVpcs"
        )
        ec2_client = MagicMock()
        ec2_client.describe_vpcs.side_effect = ec2_error

        def client_factory(service):
            if service == "ec2":
                return ec2_client
            return MagicMock()

        session.client.side_effect = client_factory
        results = check_permissions(session)
        ec2_result = next(r for r in results if r.service == "EC2")
        assert ec2_result.passed is False
        assert ec2_result.error is not None

    def test_s3_denied(self):
        session = MagicMock()
        s3_error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "ListBuckets"
        )

        ec2_client = MagicMock()
        ec2_client.describe_vpcs.side_effect = ClientError(
            {"Error": {"Code": "DryRunOperation"}}, "DescribeVpcs"
        )

        s3_client = MagicMock()
        s3_client.list_buckets.side_effect = s3_error

        def client_factory(service):
            if service == "ec2":
                return ec2_client
            if service == "s3":
                return s3_client
            return MagicMock()

        session.client.side_effect = client_factory
        results = check_permissions(session)
        s3_result = next(r for r in results if r.service == "S3")
        assert s3_result.passed is False

    def test_multiple_services_denied(self):
        session = MagicMock()
        error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "op"
        )

        def client_factory(service):
            client = MagicMock()
            # Make everything raise
            if service == "ec2":
                client.describe_vpcs.side_effect = error
            elif service == "elbv2":
                client.describe_load_balancers.side_effect = error
            elif service == "s3":
                client.list_buckets.side_effect = error
            elif service == "acm":
                client.list_certificates.side_effect = error
            elif service == "route53":
                client.list_hosted_zones.side_effect = error
            elif service == "iam":
                client.list_server_certificates.side_effect = error
            elif service == "wafv2":
                client.list_web_acls.side_effect = error
            elif service == "sts":
                client.get_caller_identity.side_effect = error
            return client

        session.client.side_effect = client_factory
        results = check_permissions(session)
        assert all(not r.passed for r in results)
        assert len(results) == 8

    def test_botocore_error_in_permission_check(self):
        from botocore.exceptions import BotoCoreError

        session = MagicMock()

        ec2_client = MagicMock()
        ec2_client.describe_vpcs.side_effect = BotoCoreError()

        def client_factory(service):
            if service == "ec2":
                return ec2_client
            return MagicMock()

        session.client.side_effect = client_factory
        results = check_permissions(session)
        ec2_result = next(r for r in results if r.service == "EC2")
        assert ec2_result.passed is False


# ---------------------------------------------------------------------------
# Hosted zones — pagination edge cases
# ---------------------------------------------------------------------------


class TestListHostedZonesPagination:
    def test_multiple_pages(self):
        mock_r53 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "HostedZones": [
                    {"Id": "/hostedzone/Z1", "Name": "first.com.",
                     "ResourceRecordSetCount": 5, "Config": {"PrivateZone": False}},
                ]
            },
            {
                "HostedZones": [
                    {"Id": "/hostedzone/Z2", "Name": "second.com.",
                     "ResourceRecordSetCount": 3, "Config": {"PrivateZone": False}},
                ]
            },
        ]
        mock_r53.get_paginator.return_value = paginator
        session = MagicMock()
        session.client.return_value = mock_r53

        zones = list_hosted_zones(session)
        assert len(zones) == 2
        assert zones[0].name == "first.com"
        assert zones[1].name == "second.com"

    def test_empty_pages(self):
        mock_r53 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{"HostedZones": []}]
        mock_r53.get_paginator.return_value = paginator
        session = MagicMock()
        session.client.return_value = mock_r53

        zones = list_hosted_zones(session)
        assert zones == []

    def test_all_private_zones_filtered(self):
        mock_r53 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "HostedZones": [
                    {"Id": "/hostedzone/Z1", "Name": "internal.",
                     "ResourceRecordSetCount": 2, "Config": {"PrivateZone": True}},
                ]
            }
        ]
        mock_r53.get_paginator.return_value = paginator
        session = MagicMock()
        session.client.return_value = mock_r53

        zones = list_hosted_zones(session)
        assert zones == []


# ---------------------------------------------------------------------------
# Key pairs — edge cases
# ---------------------------------------------------------------------------


class TestListKeyPairsEdgeCases:
    def test_missing_optional_fields(self):
        session = MagicMock()
        session.client.return_value.describe_key_pairs.return_value = {
            "KeyPairs": [
                {"KeyName": "minimal-key"},
            ]
        }
        keys = list_key_pairs(session)
        assert len(keys) == 1
        assert keys[0].name == "minimal-key"
        assert keys[0].key_id == ""
        assert keys[0].key_type == "unknown"

    def test_empty_key_pairs(self):
        session = MagicMock()
        session.client.return_value.describe_key_pairs.return_value = {"KeyPairs": []}
        keys = list_key_pairs(session)
        assert keys == []
