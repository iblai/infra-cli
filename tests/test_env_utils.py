"""Tests for iblai_infra.env_utils — the .env parser shared by
launch-env, provision-env, and setup-env."""

from __future__ import annotations

import io
from unittest import mock

from iblai_infra.env_utils import (
    load_env_file,
    mask,
    parse_bool,
    resolve_pinned_cli_ops_tag,
)


# ---------------------------------------------------------------------------
# load_env_file
# ---------------------------------------------------------------------------

class TestLoadEnvFile:
    def test_basic_kv(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("FOO=bar\nBAZ=qux\n")
        assert load_env_file(f) == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_full_line_comments_and_blanks(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("# header\n\nFOO=bar\n   # inner comment\n\nBAZ=qux\n")
        assert load_env_file(f) == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_inline_comment_on_unquoted_value(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("VPN_IP=auto       # auto-detects your IP\n")
        assert load_env_file(f) == {"VPN_IP": "auto"}

    def test_inline_comment_requires_whitespace_before_hash(self, tmp_path):
        # Operators with `#` in passwords need it preserved when there's no
        # space before — common pattern for hash-prefixed secrets.
        f = tmp_path / ".env"
        f.write_text("PASSWORD=secret#abc\n")
        assert load_env_file(f) == {"PASSWORD": "secret#abc"}

    def test_quoted_value_preserves_hash(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('PASSWORD="secret # really part of pw"\n')
        assert load_env_file(f) == {"PASSWORD": "secret # really part of pw"}

    def test_strips_surrounding_double_quotes(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('FOO="bar baz"\n')
        assert load_env_file(f) == {"FOO": "bar baz"}

    def test_strips_surrounding_single_quotes(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("FOO='bar baz'\n")
        assert load_env_file(f) == {"FOO": "bar baz"}

    def test_value_with_equals_sign_inside(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("URL=https://example.com/?a=1&b=2\n")
        assert load_env_file(f) == {"URL": "https://example.com/?a=1&b=2"}

    def test_strips_key_whitespace(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("  FOO  =bar\n")
        assert load_env_file(f) == {"FOO": "bar"}

    def test_ignores_lines_without_equals(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("not-a-kv-line\nFOO=bar\n")
        assert load_env_file(f) == {"FOO": "bar"}


# ---------------------------------------------------------------------------
# mask
# ---------------------------------------------------------------------------

class TestMask:
    def test_short_value(self):
        assert mask("short") == "****"

    def test_eight_chars_still_masked(self):
        assert mask("12345678") == "****"

    def test_long_value(self):
        assert mask("AKIAIOSFODNN7EXAMPLE") == "AKIA****MPLE"


# ---------------------------------------------------------------------------
# parse_bool
# ---------------------------------------------------------------------------

class TestParseBool:
    def test_truthy(self):
        for v in ("true", "True", "TRUE", "1", "yes", "YES"):
            assert parse_bool(v) is True

    def test_falsy(self):
        for v in ("false", "0", "no", "anything-else"):
            assert parse_bool(v) is False

    def test_none_returns_default(self):
        assert parse_bool(None) is False
        assert parse_bool(None, default=True) is True


# ---------------------------------------------------------------------------
# resolve_pinned_cli_ops_tag
# ---------------------------------------------------------------------------

PYPROJECT_WITH_PIN = b"""
[project]
name = "iblai-images"
dependencies = ["ibl-cli"]

[tool.uv.sources]
ibl-cli = { git = "https://github.com/iblai/ibl-cli-ops", rev = "5.39.0" }
"""

PYPROJECT_NO_PIN = b"""
[project]
name = "iblai-images"
dependencies = ["ibl-cli"]
"""

PYPROJECT_PATH_PIN = b"""
[project]
name = "iblai-images"

[tool.uv.sources]
ibl-cli = { path = "../iblai-cli-ops" }
"""


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestResolvePinnedCliOpsTag:
    def _resolve(self, body, **kw):
        tag = kw.pop("tag", "main")
        with mock.patch(
            "urllib.request.urlopen", return_value=_FakeResponse(body)
        ) as murl:
            result = resolve_pinned_cli_ops_tag(
                "ghp_token", "iblai", "iblai-prod-images", tag, **kw
            )
        return result, murl

    def test_resolves_rev(self):
        tag, murl = self._resolve(PYPROJECT_WITH_PIN)
        assert tag == "5.39.0"
        req = murl.call_args[0][0]
        assert (
            "repos/iblai/iblai-prod-images/contents/pyproject.toml?ref=main"
            in req.full_url
        )
        assert req.headers["Authorization"] == "Bearer ghp_token"

    def test_subdir_path_in_url(self):
        tag, murl = self._resolve(PYPROJECT_WITH_PIN, subdir="iblai-prod-images")
        assert tag == "5.39.0"
        req = murl.call_args[0][0]
        assert "/contents/iblai-prod-images/pyproject.toml?ref=main" in req.full_url

    def test_no_pin_returns_none(self):
        tag, _ = self._resolve(PYPROJECT_NO_PIN)
        assert tag is None

    def test_path_pin_returns_none(self):
        tag, _ = self._resolve(PYPROJECT_PATH_PIN)
        assert tag is None

    def test_http_error_returns_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("404")):
            assert (
                resolve_pinned_cli_ops_tag("t", "iblai", "iblai-prod-images", "main")
                is None
            )

    def test_invalid_toml_returns_none(self):
        tag, _ = self._resolve(b"not [ valid toml {{")
        assert tag is None
