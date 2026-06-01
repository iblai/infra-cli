"""Tests for validation functions in prompt modules — domain, IP, CIDR."""

from __future__ import annotations

import pytest

from iblai_infra.prompts.infrastructure import _validate_cidr, _validate_ip
from iblai_infra.prompts.dns_certs import (
    _validate_domain,
    _validate_ip_csv,
    _validate_ip_or_cidr,
)


# ---------------------------------------------------------------------------
# IP validation
# ---------------------------------------------------------------------------


class TestValidateIP:
    def test_valid_ipv4(self):
        assert _validate_ip("192.168.1.1") is True

    def test_valid_public_ip(self):
        assert _validate_ip("203.0.113.42") is True

    def test_valid_loopback(self):
        assert _validate_ip("127.0.0.1") is True

    def test_valid_ipv6(self):
        assert _validate_ip("::1") is True

    def test_valid_ipv6_full(self):
        assert _validate_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334") is True

    def test_invalid_string(self):
        result = _validate_ip("not-an-ip")
        assert isinstance(result, str)
        assert "valid" in result.lower()

    def test_empty_string(self):
        result = _validate_ip("")
        assert isinstance(result, str)

    def test_ip_with_port(self):
        result = _validate_ip("192.168.1.1:8080")
        assert isinstance(result, str)

    def test_cidr_notation_rejected(self):
        result = _validate_ip("10.0.0.0/16")
        assert isinstance(result, str)

    def test_whitespace_stripped(self):
        assert _validate_ip("  203.0.113.42  ") is True

    def test_four_octets_out_of_range(self):
        result = _validate_ip("256.1.1.1")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# CIDR validation
# ---------------------------------------------------------------------------


class TestValidateCIDR:
    def test_valid_cidr_16(self):
        assert _validate_cidr("10.0.0.0/16") is True

    def test_valid_cidr_24(self):
        assert _validate_cidr("192.168.1.0/24") is True

    def test_valid_cidr_32(self):
        assert _validate_cidr("10.0.0.1/32") is True

    def test_valid_cidr_0(self):
        assert _validate_cidr("0.0.0.0/0") is True

    def test_invalid_cidr(self):
        result = _validate_cidr("not-a-cidr")
        assert isinstance(result, str)
        assert "valid" in result.lower()

    def test_empty_string(self):
        result = _validate_cidr("")
        assert isinstance(result, str)

    def test_plain_ip_no_prefix(self):
        # Plain IP without prefix length is valid as /32
        assert _validate_cidr("10.0.0.1") is True

    def test_whitespace_stripped(self):
        assert _validate_cidr("  10.0.0.0/16  ") is True

    def test_prefix_too_large(self):
        result = _validate_cidr("10.0.0.0/33")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------


class TestValidateDomain:
    def test_valid_simple(self):
        assert _validate_domain("example.com") is True

    def test_valid_subdomain(self):
        assert _validate_domain("sub.example.com") is True

    def test_valid_with_hyphens(self):
        assert _validate_domain("my-domain.example.com") is True

    def test_valid_tld_only(self):
        assert _validate_domain("ibl.ai") is True

    def test_valid_long_tld(self):
        assert _validate_domain("example.education") is True

    def test_invalid_empty(self):
        assert _validate_domain("") is False

    def test_invalid_no_dot(self):
        assert _validate_domain("localhost") is False

    def test_invalid_spaces(self):
        assert _validate_domain("example .com") is False

    def test_invalid_special_chars(self):
        assert _validate_domain("example!.com") is False

    def test_invalid_underscore(self):
        # DNS names shouldn't have underscores (they're not alphanumeric after replacing -)
        assert _validate_domain("my_domain.com") is False

    def test_invalid_double_dot(self):
        assert _validate_domain("example..com") is False

    def test_invalid_leading_dot(self):
        assert _validate_domain(".example.com") is False

    def test_whitespace_trimmed(self):
        assert _validate_domain("  example.com  ") is True

    def test_uppercase_accepted(self):
        assert _validate_domain("Example.COM") is True

    def test_numeric_parts(self):
        assert _validate_domain("123.456") is True


# ---------------------------------------------------------------------------
# WAF IP/CIDR validation
# ---------------------------------------------------------------------------


class TestValidateIpOrCidr:
    def test_bare_ip(self):
        assert _validate_ip_or_cidr("203.0.113.7") is True

    def test_cidr(self):
        assert _validate_ip_or_cidr("10.0.0.0/16") is True

    def test_slash_32_cidr(self):
        assert _validate_ip_or_cidr("198.51.100.1/32") is True

    def test_invalid_garbage(self):
        assert _validate_ip_or_cidr("not-an-ip") is False

    def test_empty_string(self):
        assert _validate_ip_or_cidr("") is False

    def test_only_whitespace(self):
        assert _validate_ip_or_cidr("   ") is False

    def test_invalid_prefix(self):
        assert _validate_ip_or_cidr("10.0.0.0/33") is False


class TestValidateIpCsv:
    def test_single_bare_ip(self):
        assert _validate_ip_csv("203.0.113.7") is True

    def test_single_cidr(self):
        assert _validate_ip_csv("10.0.0.0/16") is True

    def test_mixed_csv(self):
        assert _validate_ip_csv("203.0.113.7, 10.0.0.0/16, 192.0.2.1") is True

    def test_trailing_comma_ok(self):
        assert _validate_ip_csv("203.0.113.7,") is True

    def test_whitespace_padding_ok(self):
        assert _validate_ip_csv("  203.0.113.7 , 10.0.0.0/16  ") is True

    def test_empty_string_rejected(self):
        result = _validate_ip_csv("")
        assert isinstance(result, str)
        assert "at least one" in result.lower()

    def test_only_commas_rejected(self):
        result = _validate_ip_csv(",,,")
        assert isinstance(result, str)
        assert "at least one" in result.lower()

    def test_one_bad_token_in_list_reported(self):
        result = _validate_ip_csv("203.0.113.7,not-an-ip,10.0.0.0/16")
        assert isinstance(result, str)
        assert "not-an-ip" in result

    def test_all_bad_tokens_reported(self):
        result = _validate_ip_csv("foo,bar")
        assert isinstance(result, str)
        assert "foo" in result and "bar" in result
