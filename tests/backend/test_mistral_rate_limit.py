from __future__ import annotations

from vibe.core.llm.backend.mistral import parse_rate_limit_headers


def test_parses_standard_headers():
    headers = {
        "x-ratelimit-limit-tokens": "500000",
        "x-ratelimit-remaining-tokens": "330000",
    }
    info = parse_rate_limit_headers(headers)
    assert info is not None
    assert info.limit_tokens == 500_000
    assert info.remaining_tokens == 330_000


def test_parses_bysize_variant():
    headers = {
        "x-ratelimitbysize-limit": "500000",
        "x-ratelimitbysize-remaining": "120000",
    }
    info = parse_rate_limit_headers(headers)
    assert info is not None
    assert info.limit_tokens == 500_000
    assert info.remaining_tokens == 120_000


def test_returns_none_when_missing_or_malformed():
    assert parse_rate_limit_headers({}) is None
    assert parse_rate_limit_headers({"x-ratelimit-limit-tokens": "abc"}) is None
    assert (
        parse_rate_limit_headers({"x-ratelimit-limit-tokens": "100"}) is None
    )  # 缺 remaining
