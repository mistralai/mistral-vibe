"""Resolves the active provider's credential for a live Unified session.

The Harness is forbidden to read a keyring, a ``.env`` file, or a
``ProviderConfig``, so it holds a port and Vibe holds this service. Resolution
happens at the point of a model call rather than at session open, because a
credential that can expire mid-session cannot be a value frozen into a
derivation: a Google ADC token outlives its session by roughly an hour, and a
re-exported environment variable or a ``/config`` provider switch has no
derivation to ride in on.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import TYPE_CHECKING

from mistralai_vibe_local_harness.vibe import (
    ProviderAuthRequired,
    ProviderCredentialResult,
    ProviderCredentialSnapshot,
)
from mistralai_vibe_local_harness.vibe.completion_profile import ANTHROPIC_VERSION
from vibe.core.config.models import MissingAPIKeyError
from vibe.core.platform_auth import (
    UNAUTHORIZED_AFTER_REFRESH_MESSAGE,
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialFactory,
    PlatformCredentialIdentity,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
)
from vibe.core.platform_auth._mistral import platform_target, resolve_snapshot
from vibe.core.types import Backend
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key_with_origin, session_credentials
from vibe.utils.keyring import keyring_read_failure
from vibe.utils.oidc_marker import is_oidc_marker

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from mistralai_vibe_local_harness.vibe import ProviderRejectionReason
    from vibe.core.config import ProviderConfig, VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator

    ProviderSelector = Callable[[VibeConfigSchema], ProviderConfig]

__all__ = ["ProviderCredentialService"]

_BEARER_STYLES = frozenset({"openai", "openai-responses", "reasoning"})


class ProviderCredentialService:
    """Vibe's implementation of the Harness provider credential port.

    Reads the orchestrator on every resolution, so a provider switch, a
    re-exported environment variable and a Google ADC refresh are all picked up
    at the next model call without reopening the session.
    """

    def __init__(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        *,
        select_provider: ProviderSelector | None = None,
        credentials: Mapping[str, str] | None = None,
        platform_credentials: PlatformCredentialFactory | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        # The connection's first-party credential, for a Mistral provider only:
        # a third-party provider keeps its own key.
        self._platform_credentials = platform_credentials
        # The session's own keys, by environment variable name, ahead of the
        # process's: one process may serve runs that each bring a key.
        self._credentials = credentials
        # Which provider a resolution targets. Defaults to the session's active
        # provider; a caller can bind a second service to a different provider
        # (e.g. the Mistral utility provider for background title generation).
        self._select_provider = select_provider or _active_provider
        # The service caches no material of its own — ``resolve_api_key`` and
        # ``VertexCredentials`` already own whatever caching exists. All it
        # remembers is which revision a provider refused, so the same rejected
        # key is not re-sent on the next turn.
        self._rejected: dict[str, str] = {}
        # The platform credential behind each provider's last snapshot, by
        # provider name, with that snapshot's revision: a rejection is reported
        # to its owner (Desktop), who alone can replace it.
        self._platform_sources: dict[str, tuple[str, PlatformCredentialIdentity]] = {}
        # Revisions refused right after a successful refresh: their rejection
        # asks to update Vibe or sign in again.
        self._refused_after_refresh: set[str] = set()
        self._lock = asyncio.Lock()

    async def resolve(self) -> ProviderCredentialResult:
        try:
            return await self._resolve()
        except Exception as exc:
            # Never propagate: an exception out of here reaches the action
            # executor and fails the turn with an untyped error instead of the
            # typed "sign in" state the client knows how to act on.
            provider_name = self._provider_name()
            return ProviderAuthRequired(
                reason="missing", provider=provider_name, message=str(exc)
            )

    async def _resolve(self) -> ProviderCredentialResult:
        config = self._orchestrator.config
        try:
            provider = self._select_provider(config)
        except ValueError as exc:
            return ProviderAuthRequired(reason="missing", provider="", message=str(exc))

        if not provider.api_key_env_var and not provider.uses_vertex:
            return _snapshot(provider, None, {}, api_key_source=None)

        snapshot = await self._current_snapshot(provider)
        if isinstance(snapshot, ProviderAuthRequired):
            return snapshot
        if snapshot is None:
            env_var = provider.api_key_env_var or "MISTRAL_API_KEY"
            error = MissingAPIKeyError(
                env_var, provider.name, keyring_error=keyring_read_failure(env_var)
            )
            return ProviderAuthRequired(
                reason="missing", provider=provider.name, message=str(error)
            )

        async with self._lock:
            if self._rejected.get(provider.name) == snapshot.revision:
                # The provider refused exactly this material and nothing has
                # changed since. Re-sending it would only spend another turn,
                # and some providers count a repeat against a lockout.
                return ProviderAuthRequired(
                    reason="rejected",
                    provider=provider.name,
                    message=(
                        UNAUTHORIZED_AFTER_REFRESH_MESSAGE
                        if snapshot.revision in self._refused_after_refresh
                        else f"The {provider.name} credential was rejected. "
                        + (
                            "Sign in again and retry."
                            if self._is_user_token(provider.name, snapshot.revision)
                            else "Update the API key and retry."
                        )
                    ),
                )
        return snapshot

    def _is_user_token(self, provider_name: str, revision: str) -> bool:
        source = self._platform_sources.get(provider_name)
        return (
            source is not None and source[0] == revision and source[1].kind != "api_key"
        )

    async def reject(
        self, *, observed_revision: str, reason: ProviderRejectionReason
    ) -> None:
        config = self._orchestrator.config
        try:
            provider = self._select_provider(config)
        except ValueError:
            return

        try:
            current = await self._current_snapshot(provider)
        except Exception:
            # Rejection bookkeeping must not hide the original typed HTTP failure.
            current = None
        if (
            isinstance(current, ProviderCredentialSnapshot)
            and current.revision != observed_revision
        ):
            # A refresh already replaced the material this rejection names.
            # Discarding the new credential would force a re-authentication
            # nobody asked for — a plausible race on Vertex, where a token can
            # expire between resolution and send.
            return

        if await self._platform_replaced(provider, observed_revision, reason):
            # The credential's owner issued a replacement: the next resolution
            # sends it, so there is nothing to withhold.
            return

        if provider.uses_vertex:
            # Vertex mints its own tokens, so recovery is a fresh mint rather
            # than a new configuration. Clearing the shared credentials makes
            # the next resolution produce a different revision on its own, so
            # there is nothing to record.
            await asyncio.to_thread(_clear_vertex_credentials)
        else:
            async with self._lock:
                self._rejected[provider.name] = observed_revision
        logger.info(
            "Provider %s rejected the active credential (%s)", provider.name, reason
        )

    async def _current_snapshot(
        self, provider: ProviderConfig
    ) -> ProviderCredentialSnapshot | ProviderAuthRequired | None:
        run_key = bool(
            self._credentials and self._credentials.get(provider.api_key_env_var)
        )
        if (
            self._platform_credentials is not None
            and _first_party(provider)
            and not run_key
        ):
            return await self._platform_snapshot(provider)
        snapshot = await asyncio.to_thread(
            _resolve_snapshot, provider, self._credentials
        )
        if snapshot is None or not is_oidc_marker(snapshot.token):
            return snapshot
        from vibe.setup.auth._oidc_runtime import oidc_headers

        headers = await oidc_headers(provider.api_base)
        return _snapshot(
            provider,
            headers["Authorization"].removeprefix("Bearer "),
            headers,
            api_key_source="the CLI OIDC session",
            follow_redirects=False,
        )

    async def _platform_snapshot(
        self, provider: ProviderConfig
    ) -> ProviderCredentialSnapshot | ProviderAuthRequired | None:
        """The connection's credential for the provider's endpoint, never a
        fall back to an API key. ``None`` reads as a missing API key.
        """
        assert self._platform_credentials is not None
        credentials = self._platform_credentials(provider.api_key_env_var)
        resolved = await resolve_snapshot(credentials, provider.api_base)
        if isinstance(resolved, PlatformCredentialRequired):
            return _credential_required(
                provider,
                resolved,
                user_token=not isinstance(
                    credentials, ApiKeyPlatformCredentialProvider
                ),
            )
        bearer = resolved.headers.get("Authorization", "").removeprefix("Bearer ")
        revision = f"{provider.name}:{resolved.revision[:16]}"
        self._platform_sources[provider.name] = (revision, resolved.identity)
        return ProviderCredentialSnapshot(
            token=bearer or None,
            headers=resolved.headers,
            revision=revision,
            api_key_source=resolved.source,
            follow_redirects=resolved.follows_redirects,
        )

    async def _platform_replaced(
        self,
        provider: ProviderConfig,
        observed_revision: str,
        reason: ProviderRejectionReason,
    ) -> bool:
        """Report a 401 to the platform credential that was sent; whether its
        owner answered with a replacement.
        """
        source = self._platform_sources.get(provider.name)
        if (
            reason != "http_unauthorized"
            or self._platform_credentials is None
            or source is None
            or source[0] != observed_revision
        ):
            return False
        target = platform_target(provider.api_base)
        if target is None:
            return False
        try:
            replacement = await self._platform_credentials(
                provider.api_key_env_var
            ).reject(target, observed=source[1], reason="http_unauthorized")
        except Exception:
            # Rejection bookkeeping must not hide the original typed HTTP failure.
            return False
        if (
            isinstance(replacement, PlatformCredentialRequired)
            and replacement.reason == "unauthorized_after_refresh"
        ):
            self._refused_after_refresh.add(observed_revision)
        return (
            isinstance(replacement, PlatformCredentialSnapshot)
            and replacement.identity != source[1]
        )

    def _provider_name(self) -> str:
        try:
            return self._select_provider(self._orchestrator.config).name
        except Exception:
            return ""


def _credential_required(
    provider: ProviderConfig, required: PlatformCredentialRequired, *, user_token: bool
) -> ProviderAuthRequired | None:
    """Why no platform credential can be sent, in the user's terms.

    A user token (Desktop or CLI sign-in) always asks to sign in again: an API
    key would not help. For an API key, ``None`` keeps the missing-key message,
    and the CLI session behind the OIDC marker asks to sign in again.
    """
    reason = required.reason
    if reason == "unauthorized_after_refresh":
        return ProviderAuthRequired(
            reason="rejected",
            provider=provider.name,
            message=UNAUTHORIZED_AFTER_REFRESH_MESSAGE,
        )
    if not user_token and reason == "rejected":
        return ProviderAuthRequired(
            reason="rejected",
            provider=provider.name,
            message=(
                f"The {provider.name} credential was rejected. "
                "Update the API key and retry."
            ),
        )
    if not user_token and reason not in {"expired", "reauthentication_required"}:
        return None
    match reason:
        case "rejected":
            return ProviderAuthRequired(
                reason="rejected",
                provider=provider.name,
                message="Your Mistral sign-in was rejected. Sign in again and retry.",
            )
        case "expired" | "reauthentication_required":
            return ProviderAuthRequired(
                reason="expired",
                provider=provider.name,
                message="Your Mistral sign-in has expired. Sign in again and retry.",
            )
        case "transient":
            message = (
                "Your Mistral sign-in could not be checked. "
                "Retry, or sign in again if this persists."
            )
        case "invalid":
            message = (
                f"Your Mistral sign-in cannot be used for {provider.api_base}. "
                "Sign in again and retry."
            )
        case "missing":
            message = "You are not signed in to Mistral. Sign in and retry."
    return ProviderAuthRequired(
        reason="missing", provider=provider.name, message=message
    )


def _first_party(provider: ProviderConfig) -> bool:
    return provider.backend == Backend.MISTRAL and not provider.uses_vertex


def _active_provider(config: VibeConfigSchema) -> ProviderConfig:
    return config.get_provider_for_model(config.get_active_model())


def _resolve_snapshot(
    provider: ProviderConfig, credentials: Mapping[str, str] | None = None
) -> ProviderCredentialSnapshot | None:
    """Resolve one credential for ``provider``, or ``None`` when none exists.

    ``credentials``, when given, resolve before the process's own keys.

    Runs in a thread: key resolution touches the keyring and the filesystem,
    and the Vertex path performs a blocking token refresh.
    """
    if credentials:
        with session_credentials(credentials):
            return _resolve_snapshot(provider)
    if provider.uses_vertex:
        # Vertex ignores the configured key and mints a Google ADC token,
        # refreshed process-wide, so "no key configured" is never the answer
        # here — a failure to mint raises and becomes ``ProviderAuthRequired``.
        #
        # Bearer alone: Vertex carries the Anthropic version in the request
        # body, not in a header.
        token = _vertex_access_token()
        return _snapshot(
            provider, token, {"Authorization": f"Bearer {token}"}, api_key_source=None
        )

    resolved = resolve_api_key_with_origin(provider.api_key_env_var)
    if not resolved:
        return None
    token, origin = resolved
    # Described here rather than sent as a pair: the Harness owns the sentence,
    # the Host owns what counts as a source.
    return _snapshot(
        provider, token, _headers(provider, token), api_key_source=origin.describe()
    )


def _headers(provider: ProviderConfig, token: str) -> Mapping[str, str]:
    """The scheme-correct authorization headers for ``provider``.

    Mistral and the bearer styles send ``Authorization``, ``anthropic`` sends
    ``x-api-key`` alongside the version it pins.
    """
    if provider.backend == Backend.MISTRAL or provider.api_style in _BEARER_STYLES:
        return {"Authorization": f"Bearer {token}"}
    if provider.api_style == "anthropic":
        return {"x-api-key": token, "anthropic-version": ANTHROPIC_VERSION}
    # An unknown style is an OpenAI-compatible endpoint, which takes bearer.
    return {"Authorization": f"Bearer {token}"}


def _snapshot(
    provider: ProviderConfig,
    token: str | None,
    headers: Mapping[str, str],
    *,
    api_key_source: str | None,
    follow_redirects: bool = True,
) -> ProviderCredentialSnapshot:
    return ProviderCredentialSnapshot(
        token=token,
        headers=headers,
        revision=_revision(provider.name, token),
        api_key_source=api_key_source,
        follow_redirects=follow_redirects,
    )


def _revision(provider_name: str, token: str | None) -> str:
    """An opaque identifier that changes when the material does.

    A truncated digest: stable while the credential is, and carrying nothing
    from which the credential could be recovered.
    """
    material = token or ""
    return f"{provider_name}:{sha256(material.encode()).hexdigest()[:16]}"


def _vertex_access_token() -> str:
    # Imported lazily so ``google.auth`` stays off the CLI startup path for the
    # users who have no Vertex provider configured. The Harness adapter sends
    # this same process-wide token, so clearing it here is what it sends next.
    from mistralai_vibe_local_harness.vibe.adapters.generic._vertex import (
        VERTEX_CREDENTIALS,
    )

    return VERTEX_CREDENTIALS.access_token


def _clear_vertex_credentials() -> None:
    from mistralai_vibe_local_harness.vibe.adapters.generic._vertex import (
        VERTEX_CREDENTIALS,
    )

    VERTEX_CREDENTIALS.invalidate()
