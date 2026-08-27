from __future__ import annotations

import asyncio

from vibe.core.identity import IdentityGateway, IdentityResult, fetch_identity


class IdentityCache:
    """Session-scoped cache for the caller's identity.

    Fetches ``/users/me`` once per ``(base_url, api_key)`` and caches successes so
    the consumers that need it (experiment init's ``organizationId`` lookup and
    the app's identity refresh) share a single round-trip instead of each issuing
    their own. Failures are not cached, so a later caller can still retry.

    Use :meth:`resolve` to fetch-or-return-cached, and :meth:`peek` for read-only
    reuse (e.g. the identity refresh) that must not trigger a fetch of its own.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: dict[tuple[str, str], IdentityResult] = {}

    async def resolve(
        self,
        *,
        base_url: str,
        api_key: str,
        gateway: IdentityGateway | None = None,
        timeout: float | None = None,
    ) -> IdentityResult | None:
        """Fetch identity once per ``(base_url, api_key)`` and cache successes.

        Single-flight so concurrent callers coalesce onto one request; failures
        are not cached so a later caller can retry.
        """
        key = (base_url, api_key)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            result = await fetch_identity(
                base_url=base_url, api_key=api_key, gateway=gateway, timeout=timeout
            )
            if result is not None:
                self._cache[key] = result
            return result

    def peek(self, *, base_url: str, api_key: str) -> IdentityResult | None:
        """Return an already-fetched identity for reuse, without fetching."""
        return self._cache.get((base_url, api_key))
