"""Tests for iblai_infra.env_utils — the .env parser shared by
launch-env and provision-env."""

from __future__ import annotations

from iblai_infra.env_utils import load_env_file, mask, parse_bool


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
