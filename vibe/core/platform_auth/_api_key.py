from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Literal

from vibe.core.platform_auth._credentials_port import (
    PlatformCredentialIdentity,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.utils.api_keys import resolve_api_key_with_origin


class ApiKeyPlatformCredentialProvider:
    """Resolve the current platform API key without freezing it into a client."""

    def __init__(self, env_var: str) -> None:
        self._env_var = env_var
        self._fixed_api_key: str | None = None
        self._rejected: dict[PlatformCredentialTarget, PlatformCredentialIdentity] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_api_key(cls, api_key: str) -> ApiKeyPlatformCredentialProvider:
        provider = cls("")
        provider._fixed_api_key = api_key
        return provider

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        snapshot = await asyncio.to_thread(self._resolve_snapshot)
        if snapshot is None:
            return PlatformCredentialRequired(reason="missing")
        async with self._lock:
            if self._rejected.get(target) == snapshot.identity:
                return PlatformCredentialRequired(
                    reason="rejected", observed_revision=snapshot.revision
                )
        return snapshot

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult:
        del reason
        current = await asyncio.to_thread(self._resolve_snapshot)
        if current is not None and current.identity != observed:
            return current
        async with self._lock:
            self._rejected[target] = observed
        return PlatformCredentialRequired(
            reason="rejected", observed_revision=observed.revision
        )

    def _resolve_snapshot(self) -> PlatformCredentialSnapshot | None:
        if self._fixed_api_key is not None:
            if not self._fixed_api_key:
                return None
            return _snapshot(
                self._fixed_api_key,
                cache_namespace="api_key:fixed",
                source="the provided credential",
            )
        return _resolve_api_key_snapshot(self._env_var)


def _resolve_api_key_snapshot(env_var: str) -> PlatformCredentialSnapshot | None:
    resolved = resolve_api_key_with_origin(env_var)
    if resolved is None:
        return None
    api_key, origin = resolved
    return _snapshot(
        api_key, cache_namespace=f"api_key:{env_var}", source=origin.describe()
    )


def _snapshot(
    api_key: str, *, cache_namespace: str, source: str
) -> PlatformCredentialSnapshot:
    return PlatformCredentialSnapshot(
        kind="api_key",
        headers={"Authorization": f"Bearer {api_key}"},
        cache_namespace=cache_namespace,
        revision=f"api_key:{sha256(api_key.encode()).hexdigest()}",
        source=source,
    )


__all__ = ["ApiKeyPlatformCredentialProvider"]
