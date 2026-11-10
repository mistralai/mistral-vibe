"""The Mistral platform credential a config's first-party calls use.

Every first-party caller (account, identity, experiments, telemetry, org
config, model catalog) takes a ``PlatformCredentialFactory``: the connection's
selection (``vibe.app_server._credential_selection``), or the API key by
default. None of them reads an API key itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx2

from vibe.core.platform_auth._api_key import ApiKeyPlatformCredentialProvider
from vibe.core.platform_auth._credentials_port import (
    CachedPrincipal,
    PlatformCredentialFactory,
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

if TYPE_CHECKING:
    from vibe.core.config import ProviderConfig, VibeConfigSchema

_PRINCIPAL_TIMEOUT_SECONDS = 5.0
# A user token's principal: unlike an API key's, it does not name the material
# a request carried, so a response is cached only for the one it was fetched for.
OIDC_PRINCIPAL_PREFIX = "oidc:v1:"


def api_key_credentials(env_var: str) -> PlatformCredentialProvider:
    return ApiKeyPlatformCredentialProvider(env_var)


@dataclass(frozen=True, slots=True)
class MistralCredentials:
    """A config's Mistral provider and the credential its calls carry."""

    provider: ProviderConfig
    credentials: PlatformCredentialProvider

    def cached_principal(self) -> str | None:
        if isinstance(self.credentials, CachedPrincipal):
            return self.credentials.cached_principal()
        return None


def mistral_credentials(
    config: VibeConfigSchema, credentials: PlatformCredentialFactory | None = None
) -> MistralCredentials | None:
    """The config's Mistral provider with its credential; ``None`` without one.

    Prefers the active provider when it is a Mistral provider, else the first
    configured one: a third-party credential never reaches a first-party
    endpoint. ``None`` also when the credential is known to be absent (no API
    key, no stored sign-in), so callers skip the request.
    """
    try:
        provider = config.get_mistral_provider()
        if provider is None:
            return None
        selected = (credentials or api_key_credentials)(provider.api_key_env_var)
        if isinstance(selected, CachedPrincipal) and not selected.has_credential():
            return None
    except Exception:
        # A first-party call is optional: an unreadable credential skips it.
        return None
    return MistralCredentials(provider, selected)


async def resolve_principal(
    mistral: MistralCredentials, base_url: str | None = None
) -> str | None:
    """The principal a fetch starts under, resolving (and refreshing) the
    credential when nothing is cached. ``None`` when it cannot be resolved in
    time: fetch, but cache nothing.
    """
    if (principal := mistral.cached_principal()) is not None:
        return principal
    try:
        target = PlatformCredentialTarget(base_url or mistral.provider.api_base)
        async with asyncio.timeout(_PRINCIPAL_TIMEOUT_SECONDS):
            snapshot = await mistral.credentials.resolve(target)
    except Exception:
        return None
    if isinstance(snapshot, PlatformCredentialRequired):
        return None
    return snapshot.principal


def is_user_principal(key: str) -> bool:
    from vibe.utils.oidc_marker import OIDC_CACHE_KEY_PREFIX

    return key.startswith((OIDC_PRINCIPAL_PREFIX, OIDC_CACHE_KEY_PREFIX))


def may_cache_under(key: str | None, *, fetched_for: str | None) -> bool:
    """Whether a response fetched for ``fetched_for`` may be stored under the
    current principal ``key``. An API key's principal names the material the
    request carried; a user token's does not, so a sign-in during the fetch
    makes the response another account's.
    """
    if key is None:
        return False
    return key == fetched_for or (fetched_for is None and not is_user_principal(key))


def fetched_for_current_account(
    credentials: PlatformCredentialProvider, fetched_for: str
) -> bool:
    """Whether a response fetched for the principal ``fetched_for`` is still
    the signed-in account's. An API key's principal names the material the
    request carried; a user token's does not, so a sign-in during the fetch
    makes the response another account's.
    """
    if not is_user_principal(fetched_for):
        return True
    return (
        isinstance(credentials, CachedPrincipal)
        and credentials.cached_principal() == fetched_for
    )


async def resolve_snapshot(
    credentials: PlatformCredentialProvider, url: str
) -> PlatformCredentialSnapshot | PlatformCredentialRequired:
    """The credential for one first-party URL, or why there is none."""
    target = platform_target(url)
    if target is None:
        return PlatformCredentialRequired(reason="invalid")
    return await credentials.resolve(target)


def platform_target(url: str) -> PlatformCredentialTarget | None:
    """The target a credential for ``url`` is resolved and rejected for;
    ``None`` for a URL no credential may authorize.
    """
    try:
        return PlatformCredentialTarget(_origin_and_path(url))
    except ValueError:
        return None


async def credential_request(
    credentials: PlatformCredentialProvider,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float | httpx2.Timeout | None = None,
    client: httpx2.AsyncClient | None = None,
    **kwargs: object,
) -> httpx2.Response:
    """Send one first-party request with the credential for its URL.

    Resolution is per request, so a refreshed token is picked up; a 401 tells
    the provider, which stops re-sending that exact credential. User tokens
    never follow a redirect. Raises ``PlatformCredentialUnavailable`` when no
    credential can be sent.
    """
    from vibe.core.platform_auth._http import PlatformCredentialUnavailable

    snapshot = await resolve_snapshot(credentials, url)
    if isinstance(snapshot, PlatformCredentialRequired):
        raise PlatformCredentialUnavailable(snapshot)
    request_headers = {**(headers or {}), **snapshot.headers}
    request_kwargs: dict[str, object] = {"headers": request_headers, **kwargs}
    if timeout is not None:
        request_kwargs["timeout"] = timeout
    if client is None:
        async with VibeAsyncHTTPClient(verify=build_ssl_context()) as owned:
            response = await owned.request(
                method,
                url,
                follow_redirects=snapshot.follows_redirects,
                **request_kwargs,  # type: ignore[arg-type]
            )
    else:
        response = await client.request(
            method,
            url,
            follow_redirects=snapshot.follows_redirects,
            **request_kwargs,  # type: ignore[arg-type]
        )
    if response.status_code == httpx2.codes.UNAUTHORIZED:
        await credentials.reject(
            PlatformCredentialTarget(_origin_and_path(url)),
            observed=snapshot.identity,
            reason="http_unauthorized",
        )
    return response


def _origin_and_path(url: str) -> str:
    parsed = httpx2.URL(url)
    scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme, parsed.scheme)
    return str(parsed.copy_with(scheme=scheme, query=None, fragment=None))


__all__ = [
    "OIDC_PRINCIPAL_PREFIX",
    "MistralCredentials",
    "api_key_credentials",
    "credential_request",
    "fetched_for_current_account",
    "is_user_principal",
    "may_cache_under",
    "mistral_credentials",
    "platform_target",
    "resolve_principal",
    "resolve_snapshot",
]
