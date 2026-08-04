from __future__ import annotations

import pytest

from vibe.setup.onboarding.context import (
    _normalize_origin,
    is_likely_mistral_private_cloud_domain,
    is_valid_custom_domain,
    resolve_browser_auth_urls,
)


def test_resolve_browser_auth_urls_bare_domain_adds_scheme_and_api() -> None:
    base, api = resolve_browser_auth_urls("custom.example.com")
    assert base == "https://custom.example.com"
    assert api == "https://custom.example.com/api"


def test_resolve_browser_auth_urls_strips_trailing_slash() -> None:
    base, api = resolve_browser_auth_urls("https://custom.example.com/")
    assert base == "https://custom.example.com"
    assert api == "https://custom.example.com/api"


def test_resolve_browser_auth_urls_preserves_scheme() -> None:
    base, api = resolve_browser_auth_urls("http://localhost:8080")
    assert base == "http://localhost:8080"
    assert api == "http://localhost:8080/api"


def test_resolve_browser_auth_urls_always_appends_api() -> None:
    base, api = resolve_browser_auth_urls("https://custom.example.com/api")
    assert base == "https://custom.example.com/api"
    assert api == "https://custom.example.com/api/api"


def test_normalize_origin_does_not_append_v1() -> None:
    assert _normalize_origin("https://api.custom.example.com") == (
        "https://api.custom.example.com"
    )


def test_normalize_origin_strips_trailing_slash_and_adds_scheme() -> None:
    assert _normalize_origin("api.custom.example.com/") == (
        "https://api.custom.example.com"
    )


@pytest.mark.parametrize(
    "value",
    [
        "example.com",
        "https://example.com",
        "http://example.com",
        "sub.domain.example.com",
        "https://custom.example.com/api",
        "http://localhost:8080",
        "localhost",
        "my-company.internal",
        "192.168.1.10",
        "http://[::1]:8080",
    ],
)
def test_is_valid_custom_domain_accepts_valid_urls(value: str) -> None:
    assert is_valid_custom_domain(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://",
        "https:/",
        "http://",
        "https:// ",
        "example .com",
        "https://exa mple.com",
        "ftp://example.com",
        "http://example.com:notaport",
    ],
)
def test_is_valid_custom_domain_rejects_invalid_urls(value: str) -> None:
    assert not is_valid_custom_domain(value)


@pytest.mark.parametrize(
    "value", ["console.123.mistral.ai", "https://console.123.mistral.ai"]
)
def test_is_likely_mistral_private_cloud_domain_detects_subdomains(value: str) -> None:
    assert is_likely_mistral_private_cloud_domain(value)


@pytest.mark.parametrize(
    "value",
    [
        "console.mistral.ai",
        "https://console.mistral.ai",
        "example.com",
        "localhost",
        "http://localhost:8080",
        "my-company.internal",
    ],
)
def test_is_likely_mistral_private_cloud_domain_false_for_non_private(
    value: str,
) -> None:
    assert not is_likely_mistral_private_cloud_domain(value)
