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
    ) -> None:
        self._orchestrator = orchestrator
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
                        f"The {provider.name} credential was rejected. "
                        "Update the API key and retry."
                    ),
                )
        return snapshot

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
        if current is not None and current.revision != observed_revision:
            # A refresh already replaced the material this rejection names.
            # Discarding the new credential would force a re-authentication
            # nobody asked for — a plausible race on Vertex, where a token can
            # expire between resolution and send.
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
    ) -> ProviderCredentialSnapshot | None:
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

    def _provider_name(self) -> str:
        try:
            return self._select_provider(self._orchestrator.config).name
        except Exception:
            return ""


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
