"""The CLI's own OIDC session as a platform credential.

The app server owns these tokens (keyring, refresh in ``OidcService``). It
sends the ID token with the tenant headers the platform gateway requires
(albe ``IdTokenGrant``), only to the audience and the registered web origins.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from hashlib import sha256
import time
from typing import Literal

import httpx2

from vibe.core.platform_auth import (
    PlatformCredentialIdentity,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import (
    OidcCredentialStore,
    OidcStorageError,
    OidcTokens,
)
from vibe.setup.auth._oidc_tenant import (
    OidcTenantResolver,
    OidcUnauthorized,
    TenantSelection,
    tenant_principal,
)

_NAMESPACE = "oidc_id_token:cli"
_SOURCE = "the CLI OIDC session"
# How long a refreshed token counts as "just refreshed": refused within this
# window, it is not refreshed again. Later, a 401 is an ordinary one.
_JUST_REFRESHED_S = 120.0

type _Withheld = Literal["rejected", "unauthorized_after_refresh"]


class CliOidcPlatformCredentialProvider:
    def __init__(
        self,
        tenants: OidcTenantResolver,
        *,
        id_token: Callable[[], str] | None = None,
        refresh_id_token: Callable[[str], str] | None = None,
        stored_tokens: Callable[[], OidcTokens | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenants = tenants
        self._id_token = id_token or _fresh_id_token
        self._refresh_id_token = refresh_id_token or _refreshed_id_token
        self._stored_tokens = stored_tokens or _stored_tokens
        # Tokens the platform refused, by destination, and what to answer
        # instead of sending them again.
        self._withheld: dict[tuple[PlatformCredentialTarget, str], _Withheld] = {}
        self._clock = clock
        # The revision the last refresh minted to replace a refused token, and
        # when: refused in turn right after, it is not refreshed again.
        self._refreshed: tuple[str, float] | None = None

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        try:
            config = OidcConfig.from_env()
        except OidcError:
            return PlatformCredentialRequired(reason="invalid")
        if not destination_allowed(config, target.base_url):
            return PlatformCredentialRequired(reason="invalid")
        try:
            snapshot = await self._snapshot(config)
        except OidcError:
            return PlatformCredentialRequired(reason="reauthentication_required")
        except OidcStorageError:
            return PlatformCredentialRequired(reason="missing")
        if not isinstance(snapshot, PlatformCredentialSnapshot):
            # None: a login or logout replaced the bundle between both reads.
            return snapshot or PlatformCredentialRequired(reason="transient")
        return self._unless_withheld(target, snapshot)

    def _unless_withheld(
        self, target: PlatformCredentialTarget, snapshot: PlatformCredentialSnapshot
    ) -> PlatformCredentialResult:
        withheld = self._withheld.get((target, snapshot.revision))
        if withheld is None:
            return snapshot
        return PlatformCredentialRequired(
            reason=withheld, observed_revision=snapshot.revision
        )

    async def _snapshot(
        self, config: OidcConfig
    ) -> PlatformCredentialSnapshot | PlatformCredentialRequired | None:
        token = await asyncio.to_thread(self._id_token)
        stored = await asyncio.to_thread(self._stored_tokens)
        if stored is None or stored.id_token.get_secret_value() != token:
            return None
        revision = sha256(token.encode()).hexdigest()
        try:
            tenant = await self._tenants.resolve(
                config.audience, _user(stored), token, TenantSelection.from_env()
            )
        except OidcUnauthorized:
            # Workspace discovery is the first request a session sends.
            if self._just_refreshed(revision):
                return PlatformCredentialRequired(
                    reason="unauthorized_after_refresh", observed_revision=revision
                )
            await self._refresh(revision)
            return await self._snapshot(config)
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers={"Authorization": f"Bearer {token}", **tenant.headers},
            cache_namespace=_NAMESPACE,
            revision=revision,
            source=_SOURCE,
            principal=tenant_principal(
                issuer=stored.issuer,
                client_id=stored.client_id,
                audience=stored.audience,
                subject=stored.subject,
                tenant=tenant,
            ),
        )

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult:
        del reason
        if observed.cache_namespace != _NAMESPACE:
            return PlatformCredentialRequired(reason="invalid")
        if self._just_refreshed(observed.revision):
            # Refused again right after a successful refresh: no other refresh.
            self._withheld[(target, observed.revision)] = "unauthorized_after_refresh"
            return PlatformCredentialRequired(
                reason="unauthorized_after_refresh", observed_revision=observed.revision
            )
        current = await self.resolve(target)
        if (
            isinstance(current, PlatformCredentialSnapshot)
            and current.revision != observed.revision
        ):
            return current
        self._withheld[(target, observed.revision)] = "rejected"
        try:
            await self._refresh(observed.revision)
        except OidcError:
            # Expired session or revoked refresh token: sign in again.
            return PlatformCredentialRequired(
                reason="rejected", observed_revision=observed.revision
            )
        return await self.resolve(target)

    async def _refresh(self, rejected_revision: str) -> None:
        """Replace the refused token; raises ``OidcError`` when refresh fails."""
        token = await asyncio.to_thread(self._refresh_id_token, rejected_revision)
        self._refreshed = (sha256(token.encode()).hexdigest(), self._clock())

    def _just_refreshed(self, revision: str) -> bool:
        return (
            self._refreshed is not None
            and self._refreshed[0] == revision
            and self._clock() < self._refreshed[1] + _JUST_REFRESHED_S
        )

    def has_credential(self) -> bool:
        try:
            return self._stored_tokens() is not None
        except (OidcError, OidcStorageError):
            return False

    def cached_principal(self) -> str | None:
        """The principal without refresh or discovery, for cache-only readers."""
        try:
            config = OidcConfig.from_env()
            stored = self._stored_tokens()
        except (OidcError, OidcStorageError):
            return None
        if stored is None or not stored.subject:
            return None
        tenant = self._tenants.cached(
            config.audience, _user(stored), TenantSelection.from_env()
        )
        if tenant is None:
            return None
        return tenant_principal(
            issuer=stored.issuer,
            client_id=stored.client_id,
            audience=stored.audience,
            subject=stored.subject,
            tenant=tenant,
        )


def destination_allowed(config: OidcConfig, url: str) -> bool:
    """Whether ``url`` is the audience or a registered first-party web origin."""
    destination = httpx2.URL(url)
    scheme = {"wss": "https", "ws": "http"}.get(destination.scheme, destination.scheme)
    allowed = {
        _origin(httpx2.URL(origin)) for origin in (config.audience, *config.web_origins)
    }
    return _origin(destination.copy_with(scheme=scheme)) in allowed


def _origin(url: httpx2.URL) -> tuple[str, str | None, int | None]:
    default_port = {"http": 80, "https": 443}.get(url.scheme)
    return url.scheme, url.host, url.port if url.port is not None else default_port


def _user(tokens: OidcTokens) -> str:
    return f"{tokens.issuer}\0{tokens.client_id}\0{tokens.subject}"


def _fresh_id_token() -> str:
    from vibe.setup.auth import _oidc_runtime

    return _oidc_runtime._resolve_id_token()


def _refreshed_id_token(rejected_revision: str) -> str:
    from vibe.setup.auth import _oidc_runtime

    return _oidc_runtime._refresh_id_token(rejected_revision)


def _stored_tokens() -> OidcTokens | None:
    config = OidcConfig.from_env()
    tokens = OidcCredentialStore()._load()
    if tokens is None or (tokens.issuer, tokens.client_id, tokens.audience) != (
        config.issuer,
        config.client_id,
        config.audience,
    ):
        return None
    return tokens


__all__ = ["CliOidcPlatformCredentialProvider", "destination_allowed"]
