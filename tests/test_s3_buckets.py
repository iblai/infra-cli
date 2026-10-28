"""The DM bucket check and repair (iblai_infra.s3_buckets), against a fake S3.

The fake applies S3's own rules where they matter: the first matching CORS
rule wins, a public policy is refused while BlockPublicPolicy is on, and the
policy status follows the policy and the public access block.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest
from botocore.exceptions import ClientError

from iblai_infra import s3_buckets as sb
from iblai_infra.s3_buckets import BucketFixError, BucketRole

DOMAIN = "example.com"
STATIC, MEDIA = "acme-dm-static", "acme-dm-media"


def _err(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeS3:
    def __init__(self, buckets: dict[str, dict]):
        self.buckets = buckets
        self.calls: list[str] = []

    def _b(self, name, op):
        self.calls.append(op)
        if name not in self.buckets:
            raise _err("404" if op == "HeadBucket" else "NoSuchBucket", op)
        b = self.buckets[name]
        if op in b.get("deny", ()):
            raise _err("AccessDenied", op)
        return b

    def head_bucket(self, Bucket):
        self._b(Bucket, "HeadBucket")

    def get_bucket_location(self, Bucket):
        return {"LocationConstraint": self._b(Bucket, "GetBucketLocation").get("region", "us-east-2")}

    def get_bucket_cors(self, Bucket):
        b = self._b(Bucket, "GetBucketCors")
        if b.get("cors") is None:
            raise _err("NoSuchCORSConfiguration", "GetBucketCors")
        return {"CORSRules": b["cors"]}

    def put_bucket_cors(self, Bucket, CORSConfiguration):
        self._b(Bucket, "PutBucketCors")["cors"] = CORSConfiguration["CORSRules"]

    @staticmethod
    def _public(b, bucket):
        pab = b.get("pab") or {}
        if pab.get("BlockPublicPolicy") or pab.get("RestrictPublicBuckets"):
            return False
        stmts = (b.get("policy") or {}).get("Statement", [])
        return any(sb._grants_public_read(s, bucket) for s in stmts)

    def get_bucket_policy_status(self, Bucket):
        b = self._b(Bucket, "GetBucketPolicyStatus")
        if b.get("policy") is None:
            raise _err("NoSuchBucketPolicy", "GetBucketPolicyStatus")
        return {"PolicyStatus": {"IsPublic": self._public(b, Bucket)}}

    def get_bucket_policy(self, Bucket):
        b = self._b(Bucket, "GetBucketPolicy")
        if b.get("policy") is None:
            raise _err("NoSuchBucketPolicy", "GetBucketPolicy")
        return {"Policy": json.dumps(b["policy"])}

    def put_bucket_policy(self, Bucket, Policy):
        b = self._b(Bucket, "PutBucketPolicy")
        policy = json.loads(Policy)
        if (b.get("pab") or {}).get("BlockPublicPolicy") and any(
            sb._grants_public_read(s, Bucket) for s in policy["Statement"]
        ):
            raise _err("AccessDenied", "PutBucketPolicy")
        b["policy"] = policy

    def get_public_access_block(self, Bucket):
        b = self._b(Bucket, "GetPublicAccessBlock")
        if b.get("pab") is None:
            raise _err("NoSuchPublicAccessBlockConfiguration", "GetPublicAccessBlock")
        return {"PublicAccessBlockConfiguration": dict(b["pab"])}

    def put_public_access_block(self, Bucket, PublicAccessBlockConfiguration):
        self._b(Bucket, "PutPublicAccessBlock")["pab"] = dict(PublicAccessBlockConfiguration)

    def list_objects_v2(self, Bucket, MaxKeys=1):
        objs = self._b(Bucket, "ListObjectsV2").get("objects", [])
        return {"Contents": [{"Key": k} for k in objs[:MaxKeys]]} if objs else {}


class FakeSession:
    def __init__(self, s3):
        self.s3 = s3

    def client(self, name, region_name=None):
        assert name == "s3"
        return self.s3


def _bucket_of(url: str) -> str:
    host = url.split("//", 1)[1].split("/", 1)
    return host[1].split("/", 1)[0] if host[0].startswith("s3.") else host[0].split(".s3.", 1)[0]


def _fake_http(s3: FakeS3):
    def preflight(url, origin, method, header):
        b = s3.buckets[_bucket_of(url)]
        if not b.get("cors"):
            return 403, "CORS is not enabled for this bucket."
        rule = sb._first_match(b["cors"], origin, method, header)
        return (200, origin) if rule else (403, "This CORS request is not allowed.")

    def anonymous_head(url):
        name = _bucket_of(url)
        return 200 if FakeS3._public(s3.buckets[name], name) else 403

    return preflight, anonymous_head


PUBLIC_READ = {"Version": "2012-10-17", "Statement": [sb._public_read_statement(STATIC)]}
ALL_BLOCKED = {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}
OPEN_PAB = {"BlockPublicAcls": False, "IgnorePublicAcls": False, "BlockPublicPolicy": False, "RestrictPublicBuckets": False}


def _good():
    return {
        STATIC: {"cors": [sb.required_cors_rule(BucketRole.STATIC, DOMAIN)], "policy": json.loads(json.dumps(PUBLIC_READ)), "pab": dict(OPEN_PAB), "objects": ["a.css"]},
        MEDIA: {"cors": [sb.required_cors_rule(BucketRole.MEDIA, DOMAIN)], "pab": dict(ALL_BLOCKED), "objects": ["u/1.png"]},
    }


def _run(s3: FakeS3):
    pre, head = _fake_http(s3)
    return sb.check_dm_buckets(FakeSession(s3), static_bucket=STATIC, media_bucket=MEDIA,
                               base_domain=DOMAIN, region="us-east-2", preflight=pre, anonymous_head=head)


def _by(report, bucket, check):
    return next(f for f in report.findings if f.bucket == bucket and f.check == check)


class TestRules:
    def test_media_rule_allows_the_chat_upload(self):
        rule = sb.required_cors_rule(BucketRole.MEDIA, DOMAIN)
        assert {"PUT", "POST", "GET", "HEAD"} <= set(rule["AllowedMethods"])
        assert rule["ExposeHeaders"] == ["ETag"]
        assert rule["AllowedOrigins"] == ["https://example.com", "https://*.example.com"]
        assert sb.cors_satisfied([rule], BucketRole.MEDIA, DOMAIN)

    def test_static_rule_is_read_only_from_anywhere(self):
        rule = sb.required_cors_rule(BucketRole.STATIC, DOMAIN)
        assert rule["AllowedMethods"] == ["GET", "HEAD"] and rule["AllowedOrigins"] == ["*"]

    def test_extra_origins_are_added_once(self):
        origins = sb.platform_origins(DOMAIN, ("https://tenant.example.org", "https://example.com"))
        assert origins == ["https://example.com", "https://*.example.com", "https://tenant.example.org"]

    def test_an_earlier_rule_without_etag_shadows_a_later_one(self):
        shadow = {"AllowedOrigins": ["*"], "AllowedMethods": ["PUT"], "AllowedHeaders": ["*"]}
        good = sb.required_cors_rule(BucketRole.MEDIA, DOMAIN)
        assert not sb.cors_satisfied([shadow, good], BucketRole.MEDIA, DOMAIN)
        assert sb.cors_satisfied([good, shadow], BucketRole.MEDIA, DOMAIN)

    def test_a_rule_for_other_origins_does_not_count(self):
        other = {"AllowedOrigins": ["https://other.org"], "AllowedMethods": ["PUT"], "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"]}
        assert not sb.cors_satisfied([other], BucketRole.MEDIA, DOMAIN)

    def test_a_dotted_bucket_name_uses_path_style(self):
        assert sb.bucket_url("a.b", "us-east-2", "k") == "https://s3.us-east-2.amazonaws.com/a.b/k"
        assert sb.bucket_url("ab", "us-east-2", "k") == "https://ab.s3.us-east-2.amazonaws.com/k"


class TestCheck:
    def test_correct_buckets_pass(self):
        report = _run(FakeS3(_good()))
        assert report.ok and not report.unknown

    def test_a_media_bucket_without_cors_fails_the_browser_preflight(self):
        """The failure seen in production: uploads fail, the DM logs 200."""
        buckets = _good()
        buckets[MEDIA]["cors"] = None
        report = _run(FakeS3(buckets))
        f = _by(report, MEDIA, "cors")
        assert f.ok is False and "CORS is not enabled" in f.detail and "no CORS rules" in f.detail
        assert [x.check for x in report.failed] == ["cors"]

    def test_a_static_bucket_that_is_not_public_fails(self):
        buckets = _good()
        buckets[STATIC]["pab"] = dict(ALL_BLOCKED)
        assert _by(_run(FakeS3(buckets)), STATIC, "public-read").ok is False

    def test_a_public_media_bucket_fails(self):
        buckets = _good()
        buckets[MEDIA].update(pab=dict(OPEN_PAB), policy={"Statement": [sb._public_read_statement(MEDIA)]})
        assert _by(_run(FakeS3(buckets)), MEDIA, "private").ok is False

    def test_without_permission_it_probes_an_object_anonymously(self):
        buckets = _good()
        buckets[STATIC]["deny"] = {"GetBucketPolicyStatus"}
        assert _by(_run(FakeS3(buckets)), STATIC, "public-read").ok is True

    def test_without_permission_or_objects_it_says_unknown_and_does_not_fail(self):
        buckets = _good()
        buckets[MEDIA].update(deny={"GetBucketPolicyStatus"}, objects=[])
        report = _run(FakeS3(buckets))
        assert _by(report, MEDIA, "private").ok is None and report.ok

    def test_cors_is_judged_by_the_preflight_even_without_read_permission(self):
        buckets = _good()
        buckets[MEDIA].update(cors=None, deny={"GetBucketCors"})
        assert _by(_run(FakeS3(buckets)), MEDIA, "cors").ok is False

    def test_a_missing_bucket_cannot_be_fixed(self):
        buckets = _good()
        del buckets[MEDIA]
        f = _by(_run(FakeS3(buckets)), MEDIA, "exists")
        assert f.ok is False and not f.fixable


class TestEnsure:
    def _fix(self, s3, extra=()):
        report = _run(s3)
        changes = sb.ensure_dm_buckets(FakeSession(s3), report, base_domain=DOMAIN, region="us-east-2", extra_origins=extra)
        return changes, _run(s3)

    def test_adds_cors_first_and_keeps_existing_rules(self):
        buckets = _good()
        other = {"AllowedOrigins": ["https://other.org"], "AllowedMethods": ["GET"], "AllowedHeaders": ["*"]}
        buckets[MEDIA]["cors"] = [other]
        s3 = FakeS3(buckets)
        changes, after = self._fix(s3, ("https://tenant.example.org",))
        assert after.ok
        rules = buckets[MEDIA]["cors"]
        assert rules[1] == other and "https://tenant.example.org" in rules[0]["AllowedOrigins"]
        assert changes == [f"{MEDIA}: added the media CORS rule, kept 1 existing"]

    def test_repairs_the_production_shape(self):
        buckets = _good()
        buckets[MEDIA]["cors"] = None
        buckets[STATIC]["cors"] = None
        changes, after = self._fix(FakeS3(buckets))
        assert after.ok and len(changes) == 2

    def test_opens_static_by_flipping_only_the_policy_flags(self):
        buckets = _good()
        buckets[STATIC].update(pab=dict(ALL_BLOCKED), policy={"Version": "2012-10-17", "Statement": [{"Sid": "Keep", "Effect": "Deny", "Principal": "*", "Action": "s3:DeleteObject", "Resource": f"arn:aws:s3:::{STATIC}/*"}]})
        _, after = self._fix(FakeS3(buckets))
        assert after.ok
        assert buckets[STATIC]["pab"] == {**ALL_BLOCKED, "BlockPublicPolicy": False, "RestrictPublicBuckets": False}
        assert [s.get("Sid") for s in buckets[STATIC]["policy"]["Statement"]] == ["Keep", "PublicReadGetObject"]

    def test_closes_a_public_media_bucket(self):
        buckets = _good()
        buckets[MEDIA].update(pab=dict(OPEN_PAB), policy={"Statement": [sb._public_read_statement(MEDIA)]})
        changes, after = self._fix(FakeS3(buckets))
        assert after.ok and buckets[MEDIA]["pab"] == ALL_BLOCKED
        assert buckets[MEDIA]["policy"]["Statement"]  # neutralised, not deleted
        assert changes == [f"{MEDIA}: blocked all public access"]

    def test_nothing_is_written_when_the_buckets_are_right(self):
        s3 = FakeS3(_good())
        changes, _ = self._fix(s3)
        assert changes == [] and not [c for c in s3.calls if c.startswith("Put")]

    def test_a_refused_write_names_the_permissions(self):
        buckets = _good()
        buckets[MEDIA].update(cors=None, deny={"PutBucketCors"})
        s3 = FakeS3(buckets)
        with pytest.raises(BucketFixError, match="s3:PutBucketCORS"):
            sb.ensure_dm_buckets(FakeSession(s3), _run(s3), base_domain=DOMAIN, region="us-east-2")


class TestHttpPreflight:
    def test_returns_s3s_message_on_refusal(self, monkeypatch):
        body = b"<Error><Code>AccessForbidden</Code><Message>CORSResponse: CORS is not enabled for this bucket.</Message></Error>"

        def refuse(req, timeout):
            assert req.get_method() == "OPTIONS"
            assert req.get_header("Origin") == "https://os.example.com"
            assert req.get_header("Access-control-request-method") == "PUT"
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(body))

        monkeypatch.setattr(sb.urllib.request, "urlopen", refuse)
        status, msg = sb.http_preflight("https://b.s3.us-east-2.amazonaws.com/x", "https://os.example.com", "PUT", "content-type")
        assert status == 403 and msg == "CORSResponse: CORS is not enabled for this bucket."


class TestNoNetwork:
    """No answer from S3 says nothing about the bucket: never a false failure."""

    def _run(self, s3, head_code=0):
        return sb.check_dm_buckets(FakeSession(s3), static_bucket=STATIC, media_bucket=MEDIA, base_domain=DOMAIN,
                                   region="us-east-2", preflight=lambda *a: (0, "no response (timed out)"),
                                   anonymous_head=lambda url: head_code)

    def test_cors_falls_back_to_the_rules(self):
        buckets = _good()
        buckets[MEDIA]["cors"] = None
        report = self._run(FakeS3(buckets))
        assert _by(report, STATIC, "cors").ok is True
        assert _by(report, MEDIA, "cors").ok is False and "no rule allows PUT" in _by(report, MEDIA, "cors").detail

    def test_unreadable_rules_and_no_network_is_unknown(self):
        buckets = _good()
        buckets[MEDIA]["deny"] = {"GetBucketCors"}
        assert _by(self._run(FakeS3(buckets)), MEDIA, "cors").ok is None

    def test_an_unanswered_anonymous_probe_is_unknown_not_private(self):
        buckets = _good()
        buckets[STATIC]["deny"] = {"GetBucketPolicyStatus"}
        assert _by(self._run(FakeS3(buckets)), STATIC, "public-read").ok is None

    def test_http_helpers_return_zero_without_a_network(self, monkeypatch):
        def down(req, timeout):
            raise urllib.error.URLError("timed out")
        monkeypatch.setattr(sb.urllib.request, "urlopen", down)
        assert sb.http_preflight("https://b.s3.us-east-2.amazonaws.com/x", "https://os.example.com", "GET", "")[0] == 0
        assert sb.http_anonymous_head("https://b.s3.us-east-2.amazonaws.com/x") == 0
