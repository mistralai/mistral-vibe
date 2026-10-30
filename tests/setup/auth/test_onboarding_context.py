from __future__ import annotations

import pytest

from vibe.core.config import ProviderConfig, VibeConfigSchema
from vibe.setup.auth.onboarding_context import (
    CUSTOM_CONSOLE_URL_ENV_VAR,
    OnboardingContext,
    _normalize_origin,
    browser_auth_account_base,
    browser_auth_requires_origin_rewrite,
    is_valid_custom_domain,
    pinned_console_api_base,
    pinned_console_url_from_env,
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


def test_resolve_browser_auth_urls_uses_explicit_api_base_verbatim() -> None:
    base, api = resolve_browser_auth_urls(
        "https://console.x", "https://connector.x:443/api"
    )
    assert base == "https://console.x"
    assert api == "https://connector.x:443/api"


def test_resolve_browser_auth_urls_normalizes_explicit_api_base_scheme() -> None:
    base, api = resolve_browser_auth_urls("console.x", "connector.x:8443/api/")
    assert base == "https://console.x"
    assert api == "https://connector.x:8443/api"


def test_resolve_browser_auth_urls_empty_api_base_derives_default() -> None:
    base, api = resolve_browser_auth_urls("https://console.x", "")
    assert base == "https://console.x"
    assert api == "https://console.x/api"


@pytest.mark.parametrize(
    ("browser_base_url", "api_base_url"),
    [
        ("https://console.x", "https://connector.x/api"),
        ("https://console.x", "https://console.x:8443/api"),
        ("https://console.x", "http://console.x/api"),
    ],
)
def test_browser_auth_requires_origin_rewrite_true_when_origins_differ(
    browser_base_url: str, api_base_url: str
) -> None:
    assert browser_auth_requires_origin_rewrite(browser_base_url, api_base_url)


@pytest.mark.parametrize(
    ("browser_base_url", "api_base_url"),
    [
        ("https://console.x", "https://console.x/api"),
        ("https://console.x", "https://console.x:443/api"),
        ("https://console.x:443", "https://console.x/api"),
        ("http://console.x:80", "http://console.x/api"),
    ],
)
def test_browser_auth_requires_origin_rewrite_false_when_origins_match(
    browser_base_url: str, api_base_url: str
) -> None:
    assert not browser_auth_requires_origin_rewrite(browser_base_url, api_base_url)


def test_browser_auth_requires_origin_rewrite_tolerates_bad_port() -> None:
    # A malformed port in hand-edited config must not raise.
    assert browser_auth_requires_origin_rewrite(
        "https://console.x", "https://connector.x:999999/api"
    )


def test_browser_auth_account_base_uses_api_origin() -> None:
    assert (
        browser_auth_account_base("https://console.x", "https://connector.x:443/api")
        == "https://connector.x:443"
    )


def test_browser_auth_account_base_falls_back_to_browser_base() -> None:
    assert browser_auth_account_base("https://console.x", None) == "https://console.x"


def test_browser_auth_account_base_single_host_is_noop() -> None:
    assert (
        browser_auth_account_base("https://console.x", "https://console.x/api")
        == "https://console.x"
    )


def test_browser_auth_account_base_single_host_preserves_path() -> None:
    # A same-origin console mounted under a path prefix must keep the prefix so
    # /whoami resolves under `{browser_base}/api/...` rather than the bare origin.
    assert (
        browser_auth_account_base(
            "https://example.com/console", "https://example.com/console/api"
        )
        == "https://example.com/console"
    )


def test_browser_auth_account_base_split_horizon_drops_api_path() -> None:
    # Distinct origins → connector origin only; the API base's own path is
    # dropped because /whoami appends the absolute `/api/vibe/whoami` path.
    assert (
        browser_auth_account_base(
            "https://console.x/console", "https://connector.x:443/api"
        )
        == "https://connector.x:443"
    )


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


@pytest.fixture
def _no_pinned_console_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CUSTOM_CONSOLE_URL_ENV_VAR, raising=False)


def test_pinned_console_url_from_env_returns_none_when_unset(
    _no_pinned_console_url: None,
) -> None:
    assert pinned_console_url_from_env() is None


@pytest.mark.parametrize("value", ["", "   "])
def test_pinned_console_url_from_env_returns_none_when_blank(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None, value: str
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, value)
    assert pinned_console_url_from_env() is None


def test_pinned_console_url_from_env_normalizes_bare_domain(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, "console.acme.internal/")
    assert pinned_console_url_from_env() == "https://console.acme.internal"


def test_pinned_console_url_from_env_keeps_explicit_scheme(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, "http://localhost:8080")
    assert pinned_console_url_from_env() == "http://localhost:8080"


@pytest.mark.parametrize("value", ["not a domain", "ftp://example.com", "http://a b"])
def test_pinned_console_url_from_env_ignores_invalid_values(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None, value: str
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, value)
    assert pinned_console_url_from_env() is None


def test_onboarding_context_load_reads_pinned_console_url_from_env(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, "https://console.acme.internal")
    assert (
        OnboardingContext.load().pinned_console_url == "https://console.acme.internal"
    )


def test_onboarding_context_load_prefers_explicit_pin_over_env(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, "https://console.acme.internal")
    context = OnboardingContext.load(
        pinned_console_url="https://console.other.internal"
    )
    assert context.pinned_console_url == "https://console.other.internal"


def test_onboarding_context_defaults_to_no_pin(_no_pinned_console_url: None) -> None:
    assert OnboardingContext.load().pinned_console_url is None


def test_onboarding_context_from_config_reads_pinned_console_url_from_env(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, "https://console.acme.internal")
    context = OnboardingContext.from_config(VibeConfigSchema.model_construct())
    assert context.pinned_console_url == "https://console.acme.internal"


def test_onboarding_context_load_explicit_none_pin_forces_no_pin(
    monkeypatch: pytest.MonkeyPatch, _no_pinned_console_url: None
) -> None:
    monkeypatch.setenv(CUSTOM_CONSOLE_URL_ENV_VAR, "https://console.acme.internal")
    context = OnboardingContext.load(pinned_console_url=None)
    assert context.pinned_console_url is None


def _split_provider(browser_base: str | None, api_base: str | None) -> ProviderConfig:
    return ProviderConfig(
        name="acme",
        api_base="https://api.acme.internal/v1",
        browser_auth_base_url=browser_base,
        browser_auth_api_base_url=api_base,
    )


def test_pinned_console_api_base_preserves_split_base_matching_pin() -> None:
    provider = _split_provider(
        "https://console.acme.internal", "https://connector.acme.internal/api"
    )
    assert (
        pinned_console_api_base(provider, "https://console.acme.internal")
        == "https://connector.acme.internal/api"
    )


def test_pinned_console_api_base_matches_pin_despite_slash_and_scheme() -> None:
    provider = _split_provider(
        "console.acme.internal/", "https://connector.acme.internal/api"
    )
    assert (
        pinned_console_api_base(provider, "https://console.acme.internal")
        == "https://connector.acme.internal/api"
    )


@pytest.mark.parametrize(
    ("browser_base", "api_base"),
    [
        # A split base of a different deployment must not be preserved.
        ("https://console.other.internal", "https://connector.acme.internal/api"),
        # No split base configured: the pin derives `{pin}/api`.
        ("https://console.acme.internal", None),
        (None, "https://connector.acme.internal/api"),
    ],
)
def test_pinned_console_api_base_returns_none_otherwise(
    browser_base: str | None, api_base: str | None
) -> None:
    provider = _split_provider(browser_base, api_base)
    assert pinned_console_api_base(provider, "https://console.acme.internal") is None
