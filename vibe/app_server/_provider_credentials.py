"""Resolves the active provider's credential for a live Unified session.

The Harness is forbidden to read a keyring, a ``.env`` file, or a
``ProviderConfig``, so it holds a port and Vibe holds this service. Resolution
happens at the point of a model call rather than at session open, because a
credential that can expire mid-session cannot be a value frozen into a
derivation: a Google ADC token outlives its session by roughly an hour, and a
re-exported environment variable or a ``/config`` provider switch has no
derivation to ride in on.

Credential *schemes* are a behavioural reference to the legacy backends and are
kept in step with them by the authentication contract test, not by comment.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import TYPE_CHECKING

# `mistralai-vibe-local-harness` is an optional extra, so an environment that never
# installs it — CI's type-check job included — cannot resolve these.
from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
    ProviderAuthRequired,
    ProviderCredentialResult,
    ProviderCredentialSnapshot,
)

from vibe.core.types import Backend
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ProviderRejectionReason,
    )

    from vibe.core.config import ProviderConfig, VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator

__all__ = ["ProviderCredentialService"]

# Kept identical to ``AnthropicAdapter.API_VERSION``; pinned by the contract
# test rather than imported, because importing it would pull the whole legacy
# backend module (and its dependencies) onto the Unified session-open path.
_ANTHROPIC_VERSION = "2023-06-01"

_BEARER_STYLES = frozenset({"openai", "openai-responses", "reasoning"})


class ProviderCredentialService:
    """Vibe's implementation of the Harness provider credential port.

    Reads the orchestrator on every resolution, so a provider switch, a
    re-exported environment variable and a Google ADC refresh are all picked up
    at the next model call without reopening the session.
    """

    def __init__(self, orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> None:
        self._orchestrator = orchestrator
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
            provider = config.get_provider_for_model(config.get_active_model())
        except ValueError as exc:
            return ProviderAuthRequired(reason="missing", provider="", message=str(exc))

        snapshot = await asyncio.to_thread(_resolve_snapshot, provider)
        if snapshot is None:
            env_var = provider.api_key_env_var or "MISTRAL_API_KEY"
            return ProviderAuthRequired(
                reason="missing",
                provider=provider.name,
                message=(
                    f"Missing {env_var} environment variable "
                    f"for {provider.name} provider"
                ),
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
            provider = config.get_provider_for_model(config.get_active_model())
        except ValueError:
            return

        current = await asyncio.to_thread(_resolve_snapshot, provider)
        if current is not None and current.revision != observed_revision:
            # A refresh already replaced the material this rejection names.
            # Discarding the new credential would force a re-authentication
            # nobody asked for — a plausible race on Vertex, where a token can
            # expire between resolution and send.
            return

        if _uses_vertex(provider):
            # Vertex mints its own tokens, so recovery is a fresh mint rather
            # than a new configuration. Clearing the shared credentials makes
            # the next resolution produce a different revision on its own,
            # which is the recovery a legacy Vertex user gets from the
            # per-request refresh — so there is nothing to record.
            await asyncio.to_thread(_clear_vertex_credentials)
        else:
            async with self._lock:
                self._rejected[provider.name] = observed_revision
        logger.info(
            "Provider %s rejected the active credential (%s)", provider.name, reason
        )

    def _provider_name(self) -> str:
        try:
            config = self._orchestrator.config
            return config.get_provider_for_model(config.get_active_model()).name
        except Exception:
            return ""


def _uses_vertex(provider: ProviderConfig) -> bool:
    # ``MistralBackend`` ignores ``api_style`` entirely, so the backend choice
    # decides first, exactly as ``create_backend`` does.
    return (
        provider.backend != Backend.MISTRAL and provider.api_style == "vertex-anthropic"
    )


def _resolve_snapshot(provider: ProviderConfig) -> ProviderCredentialSnapshot | None:
    """Resolve one credential for ``provider``, or ``None`` when none exists.

    Runs in a thread: key resolution touches the keyring and the filesystem,
    and the Vertex path performs a blocking token refresh.
    """
    if _uses_vertex(provider):
        # Vertex ignores the configured key and mints a Google ADC token,
        # refreshed process-wide, so "no key configured" is never the answer
        # here — a failure to mint raises and becomes ``ProviderAuthRequired``.
        #
        # Bearer alone: ``VertexAnthropicAdapter`` carries the Anthropic version
        # in the request body, not in a header, so a version header here would
        # be a credential the legacy path never sends.
        token = _vertex_access_token()
        return _snapshot(provider, token, {"Authorization": f"Bearer {token}"})

    env_var = provider.api_key_env_var
    token = resolve_api_key(env_var) if env_var else None
    if not token:
        return None
    return _snapshot(provider, token, _headers(provider, token))


def _headers(provider: ProviderConfig, token: str) -> Mapping[str, str]:
    """The scheme-correct authorization headers for ``provider``.

    Mirrors the legacy adapter table: ``MistralBackend`` and the bearer styles
    send ``Authorization``, ``anthropic`` sends ``x-api-key`` alongside the
    version it pins.
    """
    if provider.backend == Backend.MISTRAL or provider.api_style in _BEARER_STYLES:
        return {"Authorization": f"Bearer {token}"}
    if provider.api_style == "anthropic":
        return {"x-api-key": token, "anthropic-version": _ANTHROPIC_VERSION}
    # An unknown style is an OpenAI-compatible endpoint as far as the legacy
    # adapter registry is concerned; bearer is the shape it would send.
    return {"Authorization": f"Bearer {token}"}


def _snapshot(
    provider: ProviderConfig, token: str, headers: Mapping[str, str]
) -> ProviderCredentialSnapshot:
    return ProviderCredentialSnapshot(
        token=token, headers=headers, revision=_revision(provider.name, token)
    )


def _revision(provider_name: str, token: str) -> str:
    """An opaque identifier that changes when the material does.

    A truncated digest: stable while the credential is, and carrying nothing
    from which the credential could be recovered.
    """
    return f"{provider_name}:{sha256(token.encode()).hexdigest()[:16]}"


def _vertex_access_token() -> str:
    # Imported lazily, as ``generic.py`` does, so ``google.auth`` stays off the
    # CLI startup path for the users who have no Vertex provider configured.
    from vibe.core.llm.backend.vertex import _CREDENTIALS

    return _CREDENTIALS.access_token


def _clear_vertex_credentials() -> None:
    """Drop the process-wide ADC credential so the next resolution re-mints.

    ``VertexCredentials`` refreshes only when its token is invalid, so a token
    that a provider refused while still unexpired would otherwise be handed
    back unchanged. Reaching past the attribute is deliberate: giving it a
    public ``invalidate`` would mean editing a legacy backend to serve the
    Unified path, which this change does not do.
    """
    from vibe.core.llm.backend import vertex

    credentials = vertex._CREDENTIALS
    with credentials._lock:
        credentials._credentials = None
