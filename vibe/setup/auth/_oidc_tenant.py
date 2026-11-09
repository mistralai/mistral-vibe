"""The organization and workspace an OIDC ID token acts in.

The token names a user, not a tenant: Mistral's records hold the memberships,
so the tenant is read over HTTP with the token itself. CLI and Desktop
connections share this resolver.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import threading
import time
from typing import Any

import httpx2
from pydantic import BaseModel

from vibe.core.platform_auth._mistral import OIDC_PRINCIPAL_PREFIX
from vibe.setup.auth._oidc_config import OidcError
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

_PAGE_SIZE = 100
# Bounds one discovery: a user in more workspaces than this picks one with
# VIBE_CLI_OIDC_WORKSPACE_ID or VIBE_CLI_OIDC_ORGANIZATION_ID.
_MAX_PAGES = 10
_PAGE_TIMEOUT_S = 10.0
_DISCOVERY_TIMEOUT_S = 30.0
# How long a failed discovery answers for the next callers before one retries.
_FAILURE_RETRY_S = 30.0
ORGANIZATION_ENV = "VIBE_CLI_OIDC_ORGANIZATION_ID"
WORKSPACE_ENV = "VIBE_CLI_OIDC_WORKSPACE_ID"


@dataclass(frozen=True, slots=True)
class OidcTenant:
    organization_id: str
    workspace_id: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        headers = {"x-external-organization-id": self.organization_id}
        if self.workspace_id:
            headers["x-external-workspace-id"] = self.workspace_id
        return headers


@dataclass(frozen=True, slots=True)
class TenantSelection:
    """An explicit organization or workspace chosen instead of discovery."""

    organization_id: str = ""
    workspace_id: str = ""

    @classmethod
    def from_env(cls) -> TenantSelection:
        return cls(
            os.environ.get(ORGANIZATION_ENV, ""), os.environ.get(WORKSPACE_ENV, "")
        )


class OidcUnauthorized(OidcError):
    """The platform refused the ID token itself (HTTP 401)."""


class _Workspace(BaseModel):
    id: str
    organization_id: str


class _Workspaces(BaseModel):
    workspaces: list[_Workspace]


type TenantDiscovery = Callable[[str, str, str], Coroutine[Any, Any, OidcTenant]]


async def discover_tenant(
    audience: str, id_token: str, workspace_id: str
) -> OidcTenant:
    """The selected workspace, else the first one the user belongs to."""
    # Producer: dashboard/users/public/routes.py:list_workspaces, ordered by
    # organization then workspace name. Its route takes the ID token without
    # tenant headers (vortex remote-auth-api-no-mandatory-customer).
    async with VibeAsyncHTTPClient(
        verify=build_ssl_context(), follow_redirects=False, timeout=_PAGE_TIMEOUT_S
    ) as client:
        for page_number in range(_MAX_PAGES):
            offset = page_number * _PAGE_SIZE
            response = await client.get(
                f"{audience.rstrip('/')}/v1/users/me/workspaces",
                params={"limit": _PAGE_SIZE, "offset": offset},
                headers={"Authorization": f"Bearer {id_token}"},
            )
            if response.status_code == httpx2.codes.UNAUTHORIZED:
                raise OidcUnauthorized("OIDC sign-in was refused; sign in again")
            response.raise_for_status()
            page = _Workspaces.model_validate(response.json())
            for workspace in page.workspaces:
                if not workspace_id or workspace.id == workspace_id:
                    return OidcTenant(workspace.organization_id, workspace.id)
            if len(page.workspaces) < _PAGE_SIZE:
                break
    raise OidcError(
        "No accessible OIDC workspace found; check membership or workspace override"
    )


type _Key = tuple[str, str, TenantSelection]


@dataclass(frozen=True, slots=True)
class _Failure:
    error: OidcError
    retry_at: float
    # Digest of the token that failed: a refused token's failure does not
    # answer for the token a refresh replaced it with.
    token_digest: str

    def answers(self, id_token: str, now: float) -> bool:
        if now >= self.retry_at:
            return False
        return not isinstance(self.error, OidcUnauthorized) or (
            self.token_digest == _digest(id_token)
        )


class _Abandoned(Exception):
    """The caller running a discovery gave up: a waiter starts its own."""


class OidcTenantResolver:
    """One tenant per user, audience and selection, discovered once.

    Keyed by the user rather than the token, so a token refresh reuses it.
    The first caller runs the discovery on its own event loop; concurrent
    callers, on any loop, wait for its answer. The lock only guards the maps,
    never the network, so ``invalidate`` (on the event loop, from a Desktop
    sign-out) returns at once. A failure answers the next callers until
    ``_FAILURE_RETRY_S``.
    """

    def __init__(
        self,
        discover: TenantDiscovery = discover_tenant,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._discover = discover
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[_Key, OidcTenant] = {}
        self._failures: dict[_Key, _Failure] = {}
        self._flights: dict[_Key, Future[OidcTenant]] = {}
        # Bumped by ``invalidate``: a discovery that started before a sign-out
        # or account switch stores nothing.
        self._generation = 0
        self._principal_generations: dict[str, int] = {}

    def cached(
        self, audience: str, principal: str, selection: TenantSelection
    ) -> OidcTenant | None:
        if selection.organization_id:
            return OidcTenant(selection.organization_id, selection.workspace_id or None)
        with self._lock:
            return self._cache.get((audience, principal, selection))

    async def resolve(
        self, audience: str, principal: str, id_token: str, selection: TenantSelection
    ) -> OidcTenant:
        key = (audience, principal, selection)
        while True:
            if (tenant := self.cached(audience, principal, selection)) is not None:
                return tenant
            with self._lock:
                failure = self._failures.get(key)
                if failure is not None and failure.answers(id_token, self._clock()):
                    raise failure.error
                flight = self._flights.get(key)
                owner = flight is None
                if flight is None:
                    flight = self._flights[key] = Future()
                started = self._generations(principal)
            if owner:
                return await self._run(key, flight, started, id_token)
            try:
                # Shielded: a waiter giving up leaves the owner's request alone.
                return await asyncio.shield(asyncio.wrap_future(flight))
            except _Abandoned:
                continue

    async def _run(
        self,
        key: _Key,
        flight: Future[OidcTenant],
        started: tuple[int, int],
        id_token: str,
    ) -> OidcTenant:
        audience, _principal, selection = key
        try:
            async with asyncio.timeout(_DISCOVERY_TIMEOUT_S):
                tenant = await self._discover(
                    audience, id_token, selection.workspace_id
                )
        except OidcError as exc:
            self._settle(key, flight, started, id_token, failure=exc)
            raise
        except Exception:
            error = OidcError(
                f"OIDC workspace discovery failed; retry or set {ORGANIZATION_ENV}"
            )
            self._settle(key, flight, started, id_token, failure=error)
            raise error from None
        except BaseException:
            self._settle(key, flight, started, id_token, failure=None)
            raise
        self._settle(key, flight, started, id_token, tenant=tenant)
        return tenant

    def invalidate(self, principal: str | None = None) -> None:
        """Forget tenants and failures; never waits on a discovery."""
        with self._lock:
            if principal is None:
                self._generation += 1
                self._cache.clear()
                self._failures.clear()
                return
            self._principal_generations[principal] = (
                self._principal_generations.get(principal, 0) + 1
            )
            for entries in (self._cache, self._failures):
                for key in [key for key in entries if key[1] == principal]:
                    del entries[key]

    def _generations(self, principal: str) -> tuple[int, int]:
        return self._generation, self._principal_generations.get(principal, 0)

    def _settle(
        self,
        key: _Key,
        flight: Future[OidcTenant],
        started: tuple[int, int],
        id_token: str,
        *,
        tenant: OidcTenant | None = None,
        failure: OidcError | None = None,
    ) -> None:
        """Answer the waiters and remember the outcome, unless an
        ``invalidate`` since makes it another session's.
        """
        with self._lock:
            if self._flights.get(key) is flight:
                del self._flights[key]
            current = self._generations(key[1]) == started
            if current and tenant is not None:
                self._cache[key] = tenant
                self._failures.pop(key, None)
            elif current and failure is not None:
                self._failures[key] = _Failure(
                    failure, self._clock() + _FAILURE_RETRY_S, _digest(id_token)
                )
        if tenant is not None:
            flight.set_result(tenant)
        else:
            flight.set_exception(failure or _Abandoned())


def _digest(id_token: str) -> str:
    return sha256(id_token.encode()).hexdigest()


def tenant_principal(
    *, issuer: str, client_id: str, audience: str, subject: str, tenant: OidcTenant
) -> str:
    """A non-secret cache identity for one user in one tenant."""
    material = json.dumps(
        [
            issuer,
            client_id,
            audience,
            subject,
            tenant.organization_id,
            tenant.workspace_id or "",
        ],
        separators=(",", ":"),
    )
    return f"{OIDC_PRINCIPAL_PREFIX}{sha256(material.encode()).hexdigest()}"


__all__ = [
    "OidcTenant",
    "OidcTenantResolver",
    "OidcUnauthorized",
    "TenantSelection",
    "discover_tenant",
    "tenant_principal",
]
