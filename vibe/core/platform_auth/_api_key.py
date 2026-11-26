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
from vibe.utils.oidc_marker import is_oidc_authorization, is_oidc_marker


class ApiKeyPlatformCredentialProvider:
    """Resolve the current platform API key without freezing it into a client."""

    def __init__(self, env_var: str, *, cli_oidc: bool = True) -> None:
        self._env_var = env_var
        # Whether the CLI OIDC marker may stand for the CLI user's session. A
        # non-CLI connection never borrows it, even when the flag is set.
        self._cli_oidc = cli_oidc
        self._fixed_api_key: str | None = None
        self._rejected: dict[PlatformCredentialTarget, PlatformCredentialIdentity] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_api_key(cls, api_key: str) -> ApiKeyPlatformCredentialProvider:
        provider = cls("")
        provider._fixed_api_key = api_key
        return provider

    def pinned(self) -> ApiKeyPlatformCredentialProvider:
        """This provider fixed to the key it resolves now: for a caller that
        keys state on the key and must fetch with that same key later, whatever
        the environment holds by then.
        """
        if self._fixed_api_key is not None:
            return self
        resolved = resolve_api_key_with_origin(self._env_var)
        return ApiKeyPlatformCredentialProvider.from_api_key(
            resolved[0] if resolved is not None else ""
        )

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        snapshot = await self._snapshot_for_target(target)
        if isinstance(snapshot, PlatformCredentialRequired):
            return snapshot
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
        current = await self._snapshot_for_target(target)
        if isinstance(current, PlatformCredentialRequired):
            return current
        if current is not None and current.identity != observed:
            return current
        async with self._lock:
            self._rejected[target] = observed
        return PlatformCredentialRequired(
            reason="rejected", observed_revision=observed.revision
        )

    async def _snapshot_for_target(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialSnapshot | PlatformCredentialRequired | None:
        snapshot = await asyncio.to_thread(self._resolve_snapshot)
        if snapshot is None or not is_oidc_authorization(
            snapshot.headers.get("Authorization")
        ):
            return snapshot
        from vibe.setup.auth._oidc_config import OidcError
        from vibe.setup.auth._oidc_runtime import oidc_headers

        try:
            headers = await oidc_headers(target.base_url)
        except OidcError:
            # Expired, refused, or misconfigured sessions need a new sign-in.
            return PlatformCredentialRequired(reason="reauthentication_required")
        revision = sha256(repr(sorted(headers.items())).encode()).hexdigest()
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers=headers,
            cache_namespace="cli-oidc",
            revision=revision,
            source="the CLI OIDC session",
            principal=await asyncio.to_thread(_marker_principal),
        )

    def has_credential(self) -> bool:
        return self._resolve_snapshot() is not None

    def cached_principal(self) -> str | None:
        snapshot = self._resolve_snapshot()
        if snapshot is None:
            return None
        if snapshot.principal is None and is_oidc_authorization(
            snapshot.headers.get("Authorization")
        ):
            return _marker_principal()
        return snapshot.principal

    def _resolve_snapshot(self) -> PlatformCredentialSnapshot | None:
        if self._fixed_api_key is not None:
            if not self._fixed_api_key:
                return None
            return _snapshot(
                self._fixed_api_key,
                cache_namespace="api_key:fixed",
                source="the provided credential",
            )
        snapshot = _resolve_api_key_snapshot(self._env_var)
        if (
            snapshot is not None
            and not self._cli_oidc
            and is_oidc_authorization(snapshot.headers.get("Authorization"))
        ):
            return None
        return snapshot


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
        # The CLI OIDC marker is shared by every account: it names nobody.
        principal=None if is_oidc_marker(api_key) else api_key_principal(api_key),
    )


def _marker_principal() -> str | None:
    # Transitional: the marker resolves the stored CLI session per request.
    from vibe.setup.auth._oidc_runtime import stored_oidc_cache_key

    return stored_oidc_cache_key()


def api_key_principal(api_key: str) -> str:
    """The anonymous per-key cache identity the on-disk caches already use
    (``vibe.core.experiments.manager.hash_api_key``).
    """
    return sha256(api_key.encode("utf-8")).hexdigest()[:32]


__all__ = ["ApiKeyPlatformCredentialProvider", "api_key_principal"]
