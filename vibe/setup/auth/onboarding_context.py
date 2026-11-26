from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit

from pydantic import HttpUrl, TypeAdapter, ValidationError

from vibe.core.config import (
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_PROVIDERS,
    DEFAULT_VIBE_BASE_URL,
    ProviderConfig,
    VibeConfigSchema,
)
from vibe.observability.logging import logger
from vibe.setup.auth.browser_sign_in_gateway import normalize_url_origin


def _normalize_origin(value: str) -> str:
    origin = value.strip()
    if "://" not in origin:
        origin = f"https://{origin}"
    return origin.rstrip("/")


_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def is_valid_custom_domain(value: str) -> bool:
    # http:// is accepted on purpose so users can target local auth gateways
    # (e.g. http://localhost:8080); _normalize_origin only upgrades scheme-less
    # inputs to https://, an explicit scheme is always respected.
    origin = value.strip()
    if "://" not in origin and ":/" in origin:
        return False
    try:
        _HTTP_URL_ADAPTER.validate_python(_normalize_origin(origin))
    except ValidationError:
        return False
    return True


def resolve_browser_auth_urls(
    domain: str, api_base_url: str | None = None
) -> tuple[str, str]:
    base = _normalize_origin(domain)
    if api_base_url:
        return base, _normalize_origin(api_base_url)
    return base, f"{base}/api"


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_key(value: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(_normalize_origin(value))
    # A malformed port in hand-edited config must not crash callers like ACP
    # auth; treat it as "no port" and let the HTTP gateway reject
    # the bad URL later. The shared normalize_url_origin raises on bad ports;
    # we swallow it here but the gateway does not.
    try:
        return normalize_url_origin(parsed)
    except ValueError:
        scheme = parsed.scheme.lower()
        return scheme, parsed.hostname, _DEFAULT_PORTS.get(scheme)


def browser_auth_requires_origin_rewrite(
    browser_base_url: str, api_base_url: str
) -> bool:
    return _origin_key(browser_base_url) != _origin_key(api_base_url)


def browser_auth_account_base(browser_base_url: str, api_base_url: str | None) -> str:
    """CLI-reachable base for account calls (/whoami, plan lookups).

    In a split-horizon deployment the browser console host is not reachable by
    the CLI, but the connector API base is: use the API base's origin so account
    calls hit the host the CLI can actually reach. In the common single-host
    case (no API base, or one that shares the browser base's origin) return the
    browser base verbatim — path included — so a console mounted under a path
    prefix keeps it and /whoami still resolves under `{browser_base}/api/...`.
    """
    browser_base = _normalize_origin(browser_base_url)
    if not api_base_url or not browser_auth_requires_origin_rewrite(
        browser_base_url, api_base_url
    ):
        return browser_base
    parsed = urlsplit(_normalize_origin(api_base_url))
    return f"{parsed.scheme}://{parsed.netloc}"


CUSTOM_CONSOLE_URL_ENV_VAR = "VIBE_CUSTOM_CONSOLE_URL"


def _find_env_value(name: str) -> str | None:
    expected_name = name.upper()
    for env_name, value in os.environ.items():
        if env_name.upper() == expected_name:
            return value
    return None


def pinned_console_url_from_env() -> str | None:
    """Administrator-pinned console URL for sign-in (`VIBE_CUSTOM_CONSOLE_URL`).

    Accepts a full URL or a bare domain (scheme-less upgrades to `https://`,
    trailing slashes stripped). Unset, empty, or invalid values mean no pin —
    an invalid value warns rather than blocking sign-in.
    """
    value = _find_env_value(CUSTOM_CONSOLE_URL_ENV_VAR)
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if not is_valid_custom_domain(stripped):
        logger.warning(
            "Ignoring invalid %s environment value: %r",
            CUSTOM_CONSOLE_URL_ENV_VAR,
            value,
        )
        return None
    return _normalize_origin(stripped)


def pinned_console_api_base(provider: ProviderConfig, pin: str) -> str | None:
    """Configured split-horizon API base belonging to the pinned console.

    The pin fixes the browser sign-in host; it must not clobber an API base
    configured for the same deployment. Only returned when the configured
    browser base normalizes to the pin.
    """
    browser_base = provider.browser_auth_base_url
    api_base = provider.browser_auth_api_base_url
    if (
        browser_base is not None
        and api_base is not None
        and _normalize_origin(browser_base) == pin
    ):
        return api_base
    return None


@dataclass(frozen=True)
class OnboardingContext:
    provider: ProviderConfig
    vibe_base_url: str = DEFAULT_VIBE_BASE_URL
    console_base_url: str = DEFAULT_CONSOLE_BASE_URL
    pinned_console_url: str | None = None
    # Why the provider is the default one, when the config could not resolve
    # an active provider: protocol surfaces report it instead of silently
    # answering over the default provider.
    config_error: str | None = None

    @property
    def supports_browser_sign_in(self) -> bool:
        return self.provider.supports_browser_sign_in

    @classmethod
    def from_config(cls, config: VibeConfigSchema) -> OnboardingContext:
        provider, config_error = resolve_active_provider_or_reason(config)
        return cls(
            provider=provider,
            vibe_base_url=config.vibe_base_url,
            console_base_url=config.console_base_url,
            pinned_console_url=pinned_console_url_from_env(),
            config_error=config_error,
        )


# The machine-readable reason surfaces report for a degraded config, in the
# app server's snake_case reason vocabulary.
CONFIG_ERROR_MODEL_INVALID = "model_config_invalid"


def resolve_active_provider_or_reason(
    config: VibeConfigSchema,
) -> tuple[ProviderConfig, str | None]:
    """The canonical "which provider is active" rule for the setup wizard and
    ACP auth: the config's active provider, or the default provider with a
    machine-readable reason when the config cannot resolve one.
    """
    try:
        return config.get_active_provider(), None
    except ValueError:
        logger.warning(
            "Config could not resolve an active provider; "
            "falling back to the default provider.",
            exc_info=True,
        )
        return DEFAULT_PROVIDERS[0], CONFIG_ERROR_MODEL_INVALID


def resolve_active_provider(config: VibeConfigSchema) -> ProviderConfig:
    """The active provider, the default when the config cannot resolve one."""
    return resolve_active_provider_or_reason(config)[0]
