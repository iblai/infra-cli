"""The provisioned DM buckets start in the configuration the platform needs.

Same rules as ``iblai_infra.s3_buckets``: media private with a CORS rule for
the browser's presigned uploads, static public-read with a read-only CORS rule.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "src/iblai_infra/terraform/templates/aws"


def _block(tf: str, rtype: str, name: str) -> str:
    start = re.search(rf'resource "{rtype}" "{name}" \{{', tf)
    assert start, f"missing {rtype}.{name}"
    depth, i = 0, start.end() - 1
    for j in range(i, len(tf)):
        depth += {"{": 1, "}": -1}.get(tf[j], 0)
        if depth == 0:
            return tf[i:j + 1]
    raise AssertionError("unbalanced braces")


@pytest.fixture(params=["single-server", "multi-server"])
def tf(request) -> str:
    return (TEMPLATES / request.param / "main.tf").read_text()


def test_media_allows_the_browsers_presigned_upload(tf):
    cors = _block(tf, "aws_s3_bucket_cors_configuration", "dm_media")
    assert "aws_s3_bucket.dm_media.id" in cors
    assert '"PUT"' in cors and '"POST"' in cors and '"GET"' in cors
    assert 'expose_headers  = ["ETag"]' in cors
    assert '"https://${var.base_domain}", "https://*.${var.base_domain}"' in cors


def test_media_is_private(tf):
    pab = _block(tf, "aws_s3_bucket_public_access_block", "dm_media")
    for flag in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
        assert re.search(rf"{flag}\s*=\s*true", pab), flag


def test_static_is_public_read_with_read_only_cors(tf):
    cors = _block(tf, "aws_s3_bucket_cors_configuration", "dm_static")
    assert 'allowed_methods = ["GET", "HEAD"]' in cors and 'allowed_origins = ["*"]' in cors
    assert '"PUT"' not in cors
    policy = _block(tf, "aws_s3_bucket_policy", "dm_static")
    assert '"s3:GetObject"' in policy and 'Principal = "*"' in policy


def test_templates_stay_ascii(tf):
    tf.encode("ascii")
