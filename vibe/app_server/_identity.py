from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from vibe.app_server.models import IdentityEntityView, IdentityView
from vibe.core.config import VibeConfigSchema
from vibe.core.identity import (
    HttpIdentityGateway,
    IdentityGateway,
    IdentityGatewayUnauthorized,
    IdentityGatewayUnavailable,
    IdentityResult,
)
from vibe.core.identity_cache import IdentityCache
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
)

__all__ = [
    "HttpIdentityGateway",
    "IdentityController",
    "IdentityGateway",
    "IdentityGatewayUnauthorized",
    "IdentityGatewayUnavailable",
    "IdentityHost",
    "IdentityResult",
]


class IdentityHost(Protocol):
    """What ``IdentityController`` needs of its session. ``AgentLoop`` satisfies
    it as it stands; the Unified backend supplies the same two members from its
    session context, so both backends reach one implementation.
    """

    @property
    def config(self) -> VibeConfigSchema: ...

    @property
    def identity_cache(self) -> IdentityCache: ...


type PlatformCredentialProviderFactory = Callable[[str], PlatformCredentialProvider]


class IdentityController:
    def __init__(
        self,
        agent_loop: IdentityHost,
        gateway: IdentityGateway | None = None,
        credentials: PlatformCredentialProviderFactory | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self._gateway = gateway or HttpIdentityGateway()
        self._credentials = credentials
        self._lock = asyncio.Lock()

    async def read(self) -> IdentityView | None:
        async with self._lock:
            return await self._read()

    async def _read(self) -> IdentityView | None:
        # The Mistral identity holds whatever the active model, like /whoami.
        provider = self._agent_loop.config.get_mistral_provider()
        if provider is None:
            return None

        if self._credentials is not None:
            credentials = self._credentials(provider.api_key_env_var)
        else:
            credentials = ApiKeyPlatformCredentialProvider(provider.api_key_env_var)

        result = await self._agent_loop.identity_cache.resolve(
            base_url=provider.api_base, credentials=credentials, gateway=self._gateway
        )
        return _to_view(result) if result is not None else None


def _to_view(result: IdentityResult) -> IdentityView:
    workspace = (
        IdentityEntityView(id=result.workspace.id, name=result.workspace.name)
        if result.workspace
        else None
    )
    organization = (
        IdentityEntityView(id=result.organization.id, name=result.organization.name)
        if result.organization
        else None
    )
    return IdentityView(
        id=result.id,
        email=result.email,
        first_name=result.first_name,
        last_name=result.last_name,
        workspace=workspace,
        organization=organization,
    )
