from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import math
import os
import threading
import time

import httpx2
from pydantic import BaseModel

from vibe.core.utils.concurrency import run_sync
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import (
    OidcCredentialStore,
    OidcStorageError,
    OidcTenant,
    OidcTokens,
)
from vibe.setup.auth._oidc_service import OidcService
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context
from vibe.utils.oidc_marker import OIDC_API_KEY, OIDC_CACHE_KEY_PREFIX

_WORKSPACE_PAGE_SIZE = 100
_refresh_lock = threading.Lock()
# Serializes workspace discovery, so concurrent requests discover once.
_tenant_lock = threading.Lock()


class _Workspace(BaseModel):
    id: str
    organization_id: str


class _Workspaces(BaseModel):
    workspaces: list[_Workspace]


def stored_oidc_credential() -> str | None:
    """The marker when a matching session is stored, else None (never raises).

    Unreadable storage or invalid configuration reads as signed out, so callers
    of ``resolve_api_key`` keep their "a key or None" contract and setup can
    still offer browser sign-in.
    """
    try:
        config = OidcConfig.from_env()
        tokens = OidcCredentialStore()._load()
    except (OidcError, OidcStorageError):
        return None
    if tokens is None or (tokens.issuer, tokens.client_id, tokens.audience) != (
        config.issuer,
        config.client_id,
        config.audience,
    ):
        return None
    return OIDC_API_KEY


def stored_oidc_cache_key() -> str | None:
    """Anonymous identity/tenant key, without token refresh or network discovery.

    An unresolved tenant is not a cache identity: bypass persistent caches until
    explicit overrides or the tenant stored with the session identify it. The
    stored tenant survives token refresh and restarts, so the key does too.
    """
    try:
        config = OidcConfig.from_env()
        tokens = OidcCredentialStore()._load()
    except (OidcError, OidcStorageError):
        return None
    if (
        tokens is None
        or not tokens.subject
        or (tokens.issuer, tokens.client_id, tokens.audience)
        != (config.issuer, config.client_id, config.audience)
    ):
        return None
    organization = os.environ.get("VIBE_CLI_OIDC_ORGANIZATION_ID", "")
    workspace = os.environ.get("VIBE_CLI_OIDC_WORKSPACE_ID", "")
    if not organization:
        tenant = _stored_tenant(tokens, workspace)
        if tenant is None:
            return None
        organization, workspace = tenant.organization_id, tenant.workspace_id
    if not organization:
        return None
    material = json.dumps(
        [
            tokens.issuer,
            tokens.client_id,
            tokens.audience,
            tokens.subject,
            organization,
            workspace,
        ],
        separators=(",", ":"),
    )
    return f"{OIDC_CACHE_KEY_PREFIX}{sha256(material.encode()).hexdigest()}"


def _stored_tenant(tokens: OidcTokens, selection: str) -> OidcTenant | None:
    """The tenant stored with ``tokens``, if discovered for this workspace
    override (``""`` for the default).
    """
    tenant = tokens.tenant
    if tenant is None or tenant.selection != selection:
        return None
    return tenant


async def oidc_headers(url: str) -> dict[str, str]:
    config = OidcConfig.from_env()
    destination = httpx2.URL(url)
    audience = httpx2.URL(config.audience)
    origins = {(audience.scheme, audience.host, audience.port)}
    for origin in map(httpx2.URL, config.web_origins):
        origins.add((origin.scheme, origin.host, origin.port))
    scheme = {"wss": "https", "ws": "http"}.get(destination.scheme, destination.scheme)
    if (scheme, destination.host, destination.port) not in origins:
        raise OidcError(
            "OIDC destination does not match the configured issuer/audience"
        )
    token = await asyncio.to_thread(_resolve_id_token)
    headers = {"Authorization": f"Bearer {token}"}
    # Albe IdTokenGrant._resolve_membership requires an explicit organization;
    # workspace is optional (albe/services/auth/grants/oidc_id_token.py).
    if not os.environ.get("VIBE_CLI_OIDC_ORGANIZATION_ID"):
        headers.update(await asyncio.to_thread(_tenant_headers, config, token))
    for suffix, header in (
        ("ORGANIZATION_ID", "x-external-organization-id"),
        ("WORKSPACE_ID", "x-external-workspace-id"),
    ):
        if value := os.environ.get(f"VIBE_CLI_OIDC_{suffix}"):
            headers[header] = value
    return headers


def _tenant_headers(config: OidcConfig, token: str) -> dict[str, str]:
    workspace = os.environ.get("VIBE_CLI_OIDC_WORKSPACE_ID", "")
    store = OidcCredentialStore()
    session = _session_for(store, token)
    if session is not None and (tenant := _stored_tenant(session, workspace)):
        return _tenant_header_values(tenant)
    with _tenant_lock:
        # Another request may have discovered it while this one waited.
        session = _session_for(store, token)
        if session is not None and (tenant := _stored_tenant(session, workspace)):
            return _tenant_header_values(tenant)
        try:
            selected = run_sync(_discover_workspace(config, token, workspace))
        except OidcError:
            raise
        except Exception:
            raise OidcError(
                "OIDC workspace discovery failed; retry or set VIBE_CLI_OIDC_ORGANIZATION_ID"
            ) from None
        tenant = OidcTenant(
            selection=workspace,
            organization_id=selected.organization_id,
            workspace_id=selected.id,
        )
        if session is not None:
            try:
                store.save_tenant(session, tenant)
            except OidcStorageError:
                # Requests still carry the tenant; persistent caches stay
                # bypassed until a later discovery saves it.
                pass
        return _tenant_header_values(tenant)


def _session_for(store: OidcCredentialStore, token: str) -> OidcTokens | None:
    """The stored session ``token`` belongs to, or ``None`` when the slot holds
    another one (a sign-in or sign-out since the token was read).
    """
    try:
        tokens = store._load()
    except OidcStorageError:
        return None
    if tokens is None or tokens.id_token.get_secret_value() != token:
        return None
    return tokens


def _tenant_header_values(tenant: OidcTenant) -> dict[str, str]:
    return {
        "x-external-organization-id": tenant.organization_id,
        "x-external-workspace-id": tenant.workspace_id,
    }


async def _discover_workspace(
    config: OidcConfig, token: str, selected_id: str
) -> _Workspace:
    # Producer: dashboard/users/public/routes.py:list_workspaces populates
    # organization_id from each workspace's organization. The list is ordered.
    async with VibeAsyncHTTPClient(
        verify=build_ssl_context(), follow_redirects=False, timeout=20
    ) as client:
        offset = 0
        while True:
            response = await client.get(
                f"{config.audience.rstrip('/')}/v1/users/me/workspaces",
                params={"limit": _WORKSPACE_PAGE_SIZE, "offset": offset},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            page = _Workspaces.model_validate(response.json())
            for workspace in page.workspaces:
                if not selected_id or workspace.id == selected_id:
                    return workspace
            if len(page.workspaces) < _WORKSPACE_PAGE_SIZE:
                raise OidcError(
                    "No accessible OIDC workspace found; check membership or workspace override"
                )
            offset += _WORKSPACE_PAGE_SIZE


def _resolve_id_token() -> str:
    # Sync credential callers and HTTP hooks share refresh-token rotation.
    with _refresh_lock:
        try:
            service = OidcService.from_env()
            tokens = run_sync(service.access_tokens())
            if not _id_token_is_fresh(tokens.id_token_expires_at):
                tokens = run_sync(service.access_tokens(force_refresh=True))
            token = tokens.id_token.get_secret_value()
            if not token or not _id_token_is_fresh(tokens.id_token_expires_at):
                raise OidcError("OIDC re-login required: ID token was not renewed")
            return token
        except OidcError:
            raise
        except Exception:
            raise OidcError(
                "OIDC credential resolution failed; explicitly re-login"
            ) from None


def _id_token_is_fresh(expires_at: float | None) -> bool:
    return (
        expires_at is not None
        and math.isfinite(expires_at)
        and expires_at > time.time() + 30
    )
