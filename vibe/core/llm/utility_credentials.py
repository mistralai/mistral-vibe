"""The connection's credential for utility calls: fast-model probe, utility
model selection, worktree naming.

Without one, these calls read the provider's API key, as they always have.
With one, a Mistral provider's calls send what the session's turns send: the
connection's sign-in (Desktop's ID token, the CLI OIDC provider) or its API
key, through the app server's provider credential service. The CLI's OIDC
marker still resolves as an API key, as it does everywhere else.

The request itself only needs ``for_provider``, as narration and image
description do. Selection and the probe cache also need two answers before
any request, offline: whether a credential is held at all, and which account
it acts for. Those come from the connection's platform credential and a run's
own keys, hence the bundle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    CachedPrincipal,
    PlatformCredentialFactory,
    PlatformCredentialProvider,
)
from vibe.core.types import Backend
from vibe.utils.api_keys import resolve_api_key, session_credentials

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import ProviderCredentialProvider
    from vibe.core.config import ProviderConfig, VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator

__all__ = [
    "UtilityCredentials",
    "UtilityCredentialsFactory",
    "credential_identity",
    "holds_credential",
    "resolve_credential_identity",
    "sends_sign_in",
    "still_signed_in_as",
]


@dataclass(frozen=True, slots=True)
class UtilityCredentials:
    """The connection's credentials, as utility calls use them.

    ``platform`` is the connection's first-party credential: it answers,
    offline, whether one is held and which account it acts for.
    ``for_provider`` is the credential a request to a provider (by name)
    sends, resolved as the session's model calls resolve it. ``run_keys``
    are a run's own API keys, by variable name: they win over both, as they do
    for the session's model calls.
    """

    platform: PlatformCredentialFactory
    for_provider: Callable[[str], ProviderCredentialProvider]
    run_keys: Mapping[str, str] | None = None


type UtilityCredentialsFactory = Callable[
    [ConfigOrchestrator[VibeConfigSchema]], UtilityCredentials
]
"""The credentials for calls made with a config of their own (worktree naming)."""


def holds_credential(
    provider: ProviderConfig, credentials: UtilityCredentials | None
) -> bool:
    """Whether a call to ``provider`` has a credential to send; offline.

    A provider without a key variable (a local server) needs none.
    """
    if (user := _user_token(provider, credentials)) is not None:
        return user.has_credential()
    return not provider.api_key_env_var or bool(_api_key(provider, credentials))


def credential_identity(
    provider: ProviderConfig, credentials: UtilityCredentials | None
) -> str | None:
    """What a verdict about ``provider`` is cached under: its API key, or the
    signed-in account. ``None`` while that account is not known yet.
    """
    if (user := _user_token(provider, credentials)) is not None:
        return user.cached_principal()
    return _api_key(provider, credentials) or ""


async def resolve_credential_identity(
    provider: ProviderConfig, credentials: UtilityCredentials | None
) -> str | None:
    """``credential_identity``, resolving the sign-in when its account is not
    known yet. ``None`` when it cannot be resolved in time.
    """
    from vibe.core.platform_auth._mistral import MistralCredentials, resolve_principal

    selected = _user_token(provider, credentials)
    if selected is None:
        return credential_identity(provider, credentials)
    return await resolve_principal(MistralCredentials(provider, selected))


def sends_sign_in(
    provider: ProviderConfig, credentials: UtilityCredentials | None
) -> bool:
    """Whether a call to ``provider`` sends the connection's sign-in rather
    than an API key.
    """
    return _user_token(provider, credentials) is not None


def still_signed_in_as(
    provider: ProviderConfig, credentials: UtilityCredentials | None, identity: str
) -> bool:
    """Whether the account ``identity`` names is still the one signed in, so a
    verdict reached under it may be kept.
    """
    if _user_token(provider, credentials) is None:
        return True
    return credential_identity(provider, credentials) == identity


def _api_key(
    provider: ProviderConfig, credentials: UtilityCredentials | None
) -> str | None:
    if credentials is not None and credentials.run_keys:
        with session_credentials(credentials.run_keys):
            return resolve_api_key(provider.api_key_env_var)
    return resolve_api_key(provider.api_key_env_var)


def _user_token(
    provider: ProviderConfig, credentials: UtilityCredentials | None
) -> _UserToken | None:
    """The connection's sign-in when ``provider``'s calls send it instead of
    an API key: a first-party provider, on a connection with a sign-in, in a
    run that brings no key of its own.
    """
    if credentials is None or provider.backend != Backend.MISTRAL:
        return None
    env_var = provider.api_key_env_var
    if not env_var or (credentials.run_keys and credentials.run_keys.get(env_var)):
        return None
    selected = credentials.platform(env_var)
    # An API key provider has the same shape as a sign-in: it is a key, and
    # keeps the key's own rules (and the CLI OIDC marker's).
    if isinstance(selected, ApiKeyPlatformCredentialProvider) or not isinstance(
        selected, _UserToken
    ):
        return None
    return selected


@runtime_checkable
class _UserToken(PlatformCredentialProvider, CachedPrincipal, Protocol):
    """A sign-in: resolved per request, and known offline."""
