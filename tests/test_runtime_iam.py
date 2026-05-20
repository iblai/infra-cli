"""Tests for iblai_infra.runtime_iam — IAM policy generator + post-provision output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iblai_infra.models import DeploymentType
from iblai_infra.runtime_iam import (
    IBLAI_ECR_ACCOUNT_ID,
    IBLAI_ECR_REGION,
    POLICY_FILENAME,
    build_runtime_iam_policy,
    extract_bucket_names,
    render_runtime_access_instructions,
)


class TestBuildPolicy:
    def test_single_bucket(self):
        policy = build_runtime_iam_policy(["my-backups"])
        assert policy["Version"] == "2012-10-17"
        sids = {s["Sid"] for s in policy["Statement"]}
        assert sids == {
            "PlatformBucketObjects",
            "PlatformBucketList",
            "ECRAuth",
            "ECRPullPlatformImages",
        }

    def test_three_buckets_arn_shape(self):
        policy = build_runtime_iam_policy([
            "p-staging-backups",
            "p-staging-dm-media",
            "p-staging-dm-static",
        ])
        objects_stmt = next(s for s in policy["Statement"] if s["Sid"] == "PlatformBucketObjects")
        list_stmt = next(s for s in policy["Statement"] if s["Sid"] == "PlatformBucketList")
        # Object-level resources get the /* suffix; bucket-level don't.
        assert objects_stmt["Resource"] == [
            "arn:aws:s3:::p-staging-backups/*",
            "arn:aws:s3:::p-staging-dm-media/*",
            "arn:aws:s3:::p-staging-dm-static/*",
        ]
        assert list_stmt["Resource"] == [
            "arn:aws:s3:::p-staging-backups",
            "arn:aws:s3:::p-staging-dm-media",
            "arn:aws:s3:::p-staging-dm-static",
        ]

    def test_s3_actions_are_tight(self):
        policy = build_runtime_iam_policy(["b"])
        obj_actions = next(s["Action"] for s in policy["Statement"] if s["Sid"] == "PlatformBucketObjects")
        assert "s3:*" not in obj_actions
        # Bucket policy / lifecycle / encryption mutations stay out.
        for forbidden in ("s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutLifecycleConfiguration"):
            assert forbidden not in obj_actions

    def test_ecr_resource_targets_iblai_account(self):
        policy = build_runtime_iam_policy(["b"])
        pull = next(s for s in policy["Statement"] if s["Sid"] == "ECRPullPlatformImages")
        assert pull["Resource"] == (
            f"arn:aws:ecr:{IBLAI_ECR_REGION}:{IBLAI_ECR_ACCOUNT_ID}:repository/*"
        )

    def test_ecr_auth_is_wildcard(self):
        # ecr:GetAuthorizationToken can ONLY be granted on Resource: "*"
        # — AWS rejects scoped ARNs for this action.
        policy = build_runtime_iam_policy(["b"])
        auth = next(s for s in policy["Statement"] if s["Sid"] == "ECRAuth")
        assert auth["Resource"] == "*"
        assert auth["Action"] == ["ecr:GetAuthorizationToken"]

    def test_empty_buckets_raises(self):
        with pytest.raises(ValueError, match="at least one S3 bucket"):
            build_runtime_iam_policy([])

    def test_policy_is_json_serializable(self):
        policy = build_runtime_iam_policy(["a", "b", "c"])
        # Round-trip — what we hand the operator must survive `aws iam put-user-policy`.
        round_tripped = json.loads(json.dumps(policy))
        assert round_tripped == policy


class TestExtractBuckets:
    def test_all_three_present(self):
        outputs = {
            "instance_public_ip": "1.2.3.4",
            "s3_bucket_backups": "p-backups",
            "s3_bucket_media": "p-media",
            "s3_bucket_static": "p-static",
        }
        assert extract_bucket_names(outputs) == ["p-backups", "p-media", "p-static"]

    def test_partial_outputs(self):
        outputs = {"s3_bucket_backups": "only-backups"}
        assert extract_bucket_names(outputs) == ["only-backups"]

    def test_no_buckets(self):
        assert extract_bucket_names({}) == []
        assert extract_bucket_names({"instance_public_ip": "1.2.3.4"}) == []

    def test_empty_string_skipped(self):
        # Terraform sometimes emits "" for an unset output rather than omitting.
        outputs = {"s3_bucket_backups": "", "s3_bucket_media": "p-m"}
        assert extract_bucket_names(outputs) == ["p-m"]


class TestRenderInstructions:
    def test_writes_policy_file(self, infra_config, tmp_path):
        outputs = {
            "s3_bucket_backups": "test-backups",
            "s3_bucket_media": "test-media",
            "s3_bucket_static": "test-static",
        }
        render_runtime_access_instructions(infra_config, outputs, tmp_path)
        policy_path = tmp_path / POLICY_FILENAME
        assert policy_path.exists()
        loaded = json.loads(policy_path.read_text())
        # File contents match what build_runtime_iam_policy emits.
        expected = build_runtime_iam_policy(["test-backups", "test-media", "test-static"])
        assert loaded == expected

    def test_call_server_skipped(self, infra_config, tmp_path):
        infra_config.deployment_type = DeploymentType.CALL
        outputs = {"s3_bucket_backups": "would-not-be-touched"}
        render_runtime_access_instructions(infra_config, outputs, tmp_path)
        assert not (tmp_path / POLICY_FILENAME).exists()

    def test_no_buckets_skips_write(self, infra_config, tmp_path):
        render_runtime_access_instructions(infra_config, outputs={}, ws=tmp_path)
        assert not (tmp_path / POLICY_FILENAME).exists()
