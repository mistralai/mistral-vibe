from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from vibe.app_server._connection_protocol import (
    PlatformCredentialsResolveParams,
    PlatformCredentialsResolveResponse,
    PlatformCredentialsSnapshot,
)
from vibe.core.platform_auth import (
    PlatformCredentialIdentity,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)

_MINIMUM_VALIDITY_MS = 60_000
_CACHE_NAMESPACE = "oauth_access_token:desktop_connection"

type PlatformCredentialsResolver = Callable[
    [PlatformCredentialsResolveParams], Awaitable[PlatformCredentialsResolveResponse]
]


class ConnectionPlatformCredentialProvider:
    """Resolve Desktop-owned access tokens without owning refresh material."""

    def __init__(self, resolver: PlatformCredentialsResolver) -> None:
        self._resolver = resolver
        self._generation = 0

    def invalidate(self) -> None:
        self._generation += 1

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        return await self._resolve(target)

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult:
        del reason
        if (
            observed.kind != "oauth_access_token"
            or observed.cache_namespace != _CACHE_NAMESPACE
        ):
            return PlatformCredentialRequired(reason="invalid")
        return await self._resolve(target, rejected_revision=observed.revision)

    async def _resolve(
        self, target: PlatformCredentialTarget, *, rejected_revision: str | None = None
    ) -> PlatformCredentialResult:
        audience = target.audience or _audience_from_base_url(target.base_url)
        generation = self._generation
        try:
            resolved = await self._resolver(
                PlatformCredentialsResolveParams(
                    audience=audience,
                    minimum_validity_ms=_MINIMUM_VALIDITY_MS,
                    rejected_revision=rejected_revision,
                )
            )
        except Exception:
            return PlatformCredentialRequired(reason="transient")
        if generation != self._generation:
            return PlatformCredentialRequired(reason="reauthentication_required")
        if not isinstance(resolved, PlatformCredentialsSnapshot):
            return PlatformCredentialRequired(reason=resolved.reason)
        if resolved.audience != audience:
            return PlatformCredentialRequired(reason="invalid")
        return PlatformCredentialSnapshot(
            kind="oauth_access_token",
            headers={"Authorization": f"Bearer {resolved.token}"},
            cache_namespace=_CACHE_NAMESPACE,
            revision=resolved.revision,
            expires_at=datetime.fromtimestamp(resolved.expires_at / 1000, tz=UTC),
            source="the Desktop OIDC session",
        )


def _audience_from_base_url(base_url: str) -> str:
    # PlatformCredentialTarget already validated and normalized the URL.
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


__all__ = ["ConnectionPlatformCredentialProvider", "PlatformCredentialsResolver"]
