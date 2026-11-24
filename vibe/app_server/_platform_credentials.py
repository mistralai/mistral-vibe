"""Desktop-owned platform credentials, resolved over the connection.

Desktop advertises one kind. An OAuth access token is sent as is; an OIDC ID
token names a user, not a tenant, so it is sent with the organization and
workspace headers the shared ``OidcTenantResolver`` reads with it.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
import uuid

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
from vibe.observability.logging import logger
from vibe.setup.auth._oidc_config import OidcError, registered_web_origin
from vibe.setup.auth._oidc_tenant import (
    OidcDiscoveryFailed,
    OidcTenantResolver,
    OidcUnauthorized,
    TenantSelection,
    tenant_principal,
)

_MINIMUM_VALIDITY_MS = 60_000


@dataclass(frozen=True, slots=True)
class SnapshotOwner:
    revision: str
    # As the client named it; None when it named none.
    organization_id: str | None
    # Issuer, client and subject of the ID token.
    user: str


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
        # The last ID-token snapshot's revision, the organization the client
        # named with it and the user it names: what another session borrowing
        # it checks against its own account.
        self._issued: SnapshotOwner | None = None
        self._user: str | None = None
        # The user a discovery is in flight for, before it is committed to
        # ``_user``. Logout during that wait must be able to drop its result.
        self._discovery_user: str | None = None

    def invalidate(self) -> None:
        self._generation += 1
        self._principal = None
        # A logout or account switch may change the user's memberships.
        stale = {
            user for user in (self._user, self._discovery_user) if user is not None
        }
        self._user = None
        self._discovery_user = None
        self._issued = None
        for user in stale:
            self._tenants.invalidate(user)

    def owner_of(self, revision: str) -> SnapshotOwner | None:
        """Who snapshot ``revision`` belongs to, when it is the last one."""
        issued = self._issued
        return issued if issued is not None and issued.revision == revision else None

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
        tenant = await self._tenant(audience, resolved.token, generation, resolved)
        if not isinstance(tenant, tuple):
            return await self._without_tenant(tenant, target, resolved.revision)
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

    async def _without_tenant(
        self,
        failure: PlatformCredentialRequired | Literal["unauthorized"],
        target: PlatformCredentialTarget,
        revision: str,
    ) -> PlatformCredentialResult:
        """Why the token cannot be sent. When workspace discovery refused it,
        Desktop decides: it refreshes the token once, and answers
        ``unauthorized_after_refresh`` when the refreshed one is refused.
        """
        if failure != "unauthorized":
            return failure
        return await self._resolve(target, rejected_revision=revision)

    async def _tenant(
        self,
        audience: str,
        id_token: str,
        generation: int,
        resolved: PlatformCredentialsSnapshot,
    ) -> (
        tuple[dict[str, str], str | None]
        | PlatformCredentialRequired
        | Literal["unauthorized"]
    ):
        """An ID token's tenant headers and principal; none for an access token.
        ``"unauthorized"`` when the platform refused the token itself.
        """
        if self._kind != "oidc_id_token":
            return {}, None
        claims = _unverified_claims(id_token)
        issuer, client_id, subject = (
            claims.get("iss"),
            _client_id(claims.get("aud")),
            claims.get("sub"),
        )
        selection = _tenant_selection(resolved)
        if not (
            isinstance(issuer, str)
            and isinstance(subject, str)
            and client_id
            and subject
        ):
            return PlatformCredentialRequired(reason="invalid")
        user = f"{issuer}\0{client_id}\0{subject}"
        self._discovery_user = user
        try:
            tenant = await self._tenants.resolve(audience, user, id_token, selection)
        except OidcUnauthorized:
            return "unauthorized"
        except OidcError as exc:
            # Discovery that got no answer is not a refusal: the network may be
            # down, or the computer just woke. Signing in again would not help.
            return PlatformCredentialRequired(
                reason=(
                    "transient"
                    if isinstance(exc, OidcDiscoveryFailed)
                    else "reauthentication_required"
                )
            )
        # A logout during discovery does not cache the answer, but still
        # returns it to whoever was waiting, including a caller that joined
        # after the logout. That answer belongs to the previous session.
        if generation != self._generation or (
            not selection.organization_id
            and self._tenants.cached(audience, user, selection) is None
        ):
            return PlatformCredentialRequired(reason="reauthentication_required")
        self._user = user
        self._issued = SnapshotOwner(
            revision=resolved.revision,
            organization_id=normalized_uuid(resolved.organization_id),
            user=user,
        )
        self._principal = tenant_principal(
            issuer=issuer,
            client_id=client_id,
            audience=audience,
            subject=subject,
            tenant=tenant,
        )
        return tenant.headers, self._principal


def _tenant_selection(resolved: PlatformCredentialsSnapshot) -> TenantSelection:
    """Which tenant ID-token requests belong to, most explicit first.

    An operator's env override wins -- any component of it: a workspace-only
    override goes to discovery filtered to that workspace, so its own
    organization answers and the pair cannot disagree. Then the tenant the
    Desktop named from its own session, unless it is malformed, in which case
    the override or discovery decides rather than failing the session.
    Without an override or a named tenant, the resolver's discovery picks one
    -- which for a user in more than one organization is the first workspace in
    the list, not necessarily the organization they work in.
    """
    override = TenantSelection.from_env()
    if (
        override.organization_id
        or override.workspace_id
        or resolved.organization_id is None
    ):
        if (override.organization_id or override.workspace_id) and (
            resolved.organization_id is not None
        ):
            logger.warning(
                "OIDC tenant override (organization=%r, workspace=%r) shadows "
                "the tenant the Desktop named (organization=%r)",
                override.organization_id,
                override.workspace_id,
                resolved.organization_id,
            )
        else:
            logger.debug(
                "OIDC tenant selection from the environment override "
                "(organization=%r, workspace=%r)",
                override.organization_id,
                override.workspace_id,
            )
        return override
    try:
        organization_id = str(uuid.UUID(resolved.organization_id))
    except ValueError:
        logger.warning(
            "Desktop named organization %r, which is not a UUID; "
            "falling back to workspace discovery",
            resolved.organization_id,
        )
        return override
    workspace_id = ""
    if resolved.workspace_id:
        try:
            workspace_id = str(uuid.UUID(resolved.workspace_id))
        except ValueError:
            logger.warning(
                "Desktop named workspace %r, which is not a UUID; "
                "keeping the organization alone",
                resolved.workspace_id,
            )
    logger.debug(
        "OIDC tenant selection from the Desktop-named session "
        "(organization=%s, workspace=%r)",
        organization_id,
        workspace_id,
    )
    return TenantSelection(organization_id=organization_id, workspace_id=workspace_id)


def normalized_uuid(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return value


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


__all__ = [
    "ConnectionPlatformCredentialProvider",
    "PlatformCredentialsResolver",
    "SnapshotOwner",
    "normalized_uuid",
]
