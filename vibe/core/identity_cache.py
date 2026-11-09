from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from vibe.core.identity import IdentityGateway, IdentityResult, fetch_identity
from vibe.core.platform_auth import (
    PlatformCredentialIdentity,
    PlatformCredentialKind,
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)


class IdentityCache:
    """Session-scoped cache for the caller's identity.

    Fetches ``/users/me`` once per credential revision and caches successes so the
    consumers that need it (experiment init's ``organizationId`` lookup and the
    app's identity refresh) share a single round-trip instead of each issuing their
    own. Failures are not cached, so a later caller can still retry.

    Use :meth:`resolve` to fetch or return the cached value.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: dict[
            tuple[PlatformCredentialTarget, PlatformCredentialKind, str, str],
            IdentityResult,
        ] = {}

    async def resolve(
        self,
        *,
        base_url: str,
        credentials: PlatformCredentialProvider,
        gateway: IdentityGateway | None = None,
        timeout: float | None = None,
    ) -> IdentityResult | None:
        """Fetch identity once per credential revision and cache successes.

        Single-flight so concurrent callers coalesce onto one request; failures
        are not cached so a later caller can retry.
        """
        try:
            target = PlatformCredentialTarget(base_url=base_url)
        except ValueError:
            return None
        async with self._lock:
            resolved = await credentials.resolve(target)
            if isinstance(resolved, PlatformCredentialRequired):
                return None
            key = (target, resolved.kind, resolved.cache_namespace, resolved.revision)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            result = await fetch_identity(
                base_url=base_url,
                credentials=_ResolvedCredentialProvider(
                    target=target, snapshot=resolved, source=credentials
                ),
                gateway=gateway,
                timeout=timeout,
            )
            if result is not None:
                self._cache[key] = result
            return result


@dataclass(frozen=True, slots=True)
class _ResolvedCredentialProvider:
    """Pin cache lookup and request send to the same resolved credential."""

    target: PlatformCredentialTarget
    snapshot: PlatformCredentialSnapshot
    source: PlatformCredentialProvider

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        if target != self.target:
            return PlatformCredentialRequired(reason="invalid")
        return self.snapshot

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult:
        return await self.source.reject(target, observed=observed, reason=reason)
