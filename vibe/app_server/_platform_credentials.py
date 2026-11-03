"""Desktop-owned platform credentials, resolved over the connection.

Desktop advertises one kind. An OAuth access token is sent as is; an OIDC ID
token names a user, not a tenant, so it is sent with the organization and
workspace headers the shared ``OidcTenantResolver`` reads with it.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import json
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from vibe.app_server._connection_protocol import (
    PlatformCredentialsKind,
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
from vibe.setup.auth._oidc_config import OidcError, registered_web_origin
from vibe.setup.auth._oidc_tenant import (
    OidcTenantResolver,
    TenantSelection,
    tenant_principal,
)

_MINIMUM_VALIDITY_MS = 60_000
_SOURCE = "the Desktop OIDC session"

type PlatformCredentialsResolver = Callable[
    [PlatformCredentialsResolveParams], Awaitable[PlatformCredentialsResolveResponse]
]


class ConnectionPlatformCredentialProvider:
    """Resolve Desktop-owned tokens without owning refresh material."""

    def __init__(
        self,
        resolver: PlatformCredentialsResolver,
        *,
        tenants: OidcTenantResolver,
        kind: PlatformCredentialsKind = "oauth_access_token",
    ) -> None:
        self._resolver = resolver
        self._kind: PlatformCredentialsKind = kind
        self._namespace = f"{kind}:desktop_connection"
        self._tenants = tenants
        self._generation = 0
        self._principal: str | None = None
        self._user: str | None = None

    def invalidate(self) -> None:
        self._generation += 1
        self._principal = None
        if self._user is not None:
            # A logout or account switch may change the user's memberships.
            self._tenants.invalidate(self._user)
            self._user = None

    def has_credential(self) -> bool:
        # Desktop owns the session: only a resolution can tell.
        return True

    def cached_principal(self) -> str | None:
        """The principal of the last snapshot, until Desktop invalidates it."""
        return self._principal

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
        if observed.kind != self._kind or observed.cache_namespace != self._namespace:
            return PlatformCredentialRequired(reason="invalid")
        return await self._resolve(target, rejected_revision=observed.revision)

    async def _resolve(
        self, target: PlatformCredentialTarget, *, rejected_revision: str | None = None
    ) -> PlatformCredentialResult:
        audience = target.audience or _audience_from_base_url(target.base_url)
        # Desktop lends its ID token for the API audience only. A registered
        # first-party web origin (chat, console) accepts that same token, so
        # it is asked for the audience and sent there by this server's rule.
        web_origin = (
            registered_web_origin(audience)
            if self._kind == "oidc_id_token" and target.audience is None
            else None
        )
        if web_origin is not None:
            audience = web_origin.audience
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
        if (
            resolved.audience != audience
            or resolved.kind != self._kind
            # Another issuer's token: the registered origins are not its own.
            or (
                web_origin is not None
                and _unverified_claims(resolved.token).get("iss") != web_origin.issuer
            )
        ):
            return PlatformCredentialRequired(reason="invalid")
        tenant = await self._tenant(audience, resolved.token, generation)
        if isinstance(tenant, PlatformCredentialRequired):
            return tenant
        tenant_headers, principal = tenant
        return PlatformCredentialSnapshot(
            kind=self._kind,
            headers={"Authorization": f"Bearer {resolved.token}", **tenant_headers},
            cache_namespace=self._namespace,
            revision=resolved.revision,
            expires_at=datetime.fromtimestamp(resolved.expires_at / 1000, tz=UTC),
            source=_SOURCE,
            principal=principal,
        )

    async def _tenant(
        self, audience: str, id_token: str, generation: int
    ) -> tuple[dict[str, str], str | None] | PlatformCredentialRequired:
        """An ID token's tenant headers and principal; none for an access token."""
        if self._kind != "oidc_id_token":
            return {}, None
        claims = _unverified_claims(id_token)
        issuer, client_id, subject = (
            claims.get("iss"),
            _client_id(claims.get("aud")),
            claims.get("sub"),
        )
        if not (
            isinstance(issuer, str)
            and isinstance(subject, str)
            and client_id
            and subject
        ):
            return PlatformCredentialRequired(reason="invalid")
        # The CLI's tenant overrides are CLI configuration: never Desktop's.
        user = f"{issuer}\0{client_id}\0{subject}"
        try:
            tenant = await self._tenants.resolve(
                audience, user, id_token, TenantSelection()
            )
        except OidcError:
            return PlatformCredentialRequired(reason="reauthentication_required")
        if generation != self._generation:
            return PlatformCredentialRequired(reason="reauthentication_required")
        self._user = user
        self._principal = tenant_principal(
            issuer=issuer,
            client_id=client_id,
            audience=audience,
            subject=subject,
            tenant=tenant,
        )
        return tenant.headers, self._principal


def _unverified_claims(token: str) -> dict[str, Any]:
    """The payload of a JWT Desktop just issued us; it is not a trust decision:
    the platform verifies the token, these claims only key the tenant cache.
    """
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _client_id(audience: object) -> str | None:
    if isinstance(audience, str):
        return audience or None
    if isinstance(audience, list) and audience and isinstance(audience[0], str):
        return audience[0]
    return None


def _audience_from_base_url(base_url: str) -> str:
    # PlatformCredentialTarget already validated and normalized the URL.
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


__all__ = ["ConnectionPlatformCredentialProvider", "PlatformCredentialsResolver"]
