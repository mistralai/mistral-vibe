"""The CLI's own OIDC session as a platform credential.

The app server owns these tokens (keyring, refresh in ``OidcService``). It
sends the ID token with the tenant headers the platform gateway requires
(albe ``IdTokenGrant``), only to the audience and the registered web origins.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from hashlib import sha256
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
    TenantSelection,
    tenant_principal,
)

_NAMESPACE = "oidc_id_token:cli"
_SOURCE = "the CLI OIDC session"


class CliOidcPlatformCredentialProvider:
    def __init__(
        self,
        tenants: OidcTenantResolver,
        *,
        id_token: Callable[[], str] | None = None,
        stored_tokens: Callable[[], OidcTokens | None] | None = None,
    ) -> None:
        self._tenants = tenants
        self._id_token = id_token or _fresh_id_token
        self._stored_tokens = stored_tokens or _stored_tokens
        self._rejected: set[tuple[PlatformCredentialTarget, str]] = set()

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
        if snapshot is not None and (target, snapshot.revision) in self._rejected:
            return PlatformCredentialRequired(
                reason="rejected", observed_revision=snapshot.revision
            )
        # None: a login or logout replaced the bundle between both reads.
        return snapshot or PlatformCredentialRequired(reason="transient")

    async def _snapshot(self, config: OidcConfig) -> PlatformCredentialSnapshot | None:
        token = await asyncio.to_thread(self._id_token)
        stored = await asyncio.to_thread(self._stored_tokens)
        if stored is None or stored.id_token.get_secret_value() != token:
            return None
        tenant = await self._tenants.resolve(
            config.audience, _user(stored), token, TenantSelection.from_env()
        )
        revision = sha256(token.encode()).hexdigest()
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
        current = await self.resolve(target)
        if (
            isinstance(current, PlatformCredentialSnapshot)
            and current.revision != observed.revision
        ):
            return current
        self._rejected.add((target, observed.revision))
        return PlatformCredentialRequired(
            reason="rejected", observed_revision=observed.revision
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
