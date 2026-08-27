from __future__ import annotations

import asyncio

from vibe.app_server.models import IdentityEntityView, IdentityView
from vibe.core.agent_loop import AgentLoop
from vibe.core.identity import (
    HttpIdentityGateway,
    IdentityGateway,
    IdentityGatewayUnauthorized,
    IdentityGatewayUnavailable,
    IdentityResult,
)
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key

__all__ = [
    "HttpIdentityGateway",
    "IdentityController",
    "IdentityGateway",
    "IdentityGatewayUnauthorized",
    "IdentityGatewayUnavailable",
    "IdentityResult",
]


class IdentityController:
    def __init__(
        self, agent_loop: AgentLoop, gateway: IdentityGateway | None = None
    ) -> None:
        self._agent_loop = agent_loop
        self._gateway = gateway or HttpIdentityGateway()
        self._lock = asyncio.Lock()

    async def read(self) -> IdentityView | None:
        async with self._lock:
            return await self._read()

    async def _read(self) -> IdentityView | None:
        runtime_config = self._agent_loop.config
        if not runtime_config.is_active_model_mistral():
            return None

        try:
            provider = runtime_config.get_active_provider()
        except ValueError:
            return None

        api_key = await asyncio.to_thread(resolve_api_key, provider.api_key_env_var)
        if not api_key:
            return None

        cached = self._agent_loop.identity_cache.peek(
            base_url=provider.api_base, api_key=api_key
        )
        if cached is not None:
            return _to_view(cached)

        result = await self._fetch(base_url=provider.api_base, api_key=api_key)
        return _to_view(result) if result is not None else None

    async def _fetch(self, *, base_url: str, api_key: str) -> IdentityResult | None:
        try:
            return await self._gateway.read(base_url=base_url, api_key=api_key)
        except IdentityGatewayUnauthorized:
            logger.info(
                "Identity endpoint rejected the API key (401/403); "
                "the key may be invalid or expired."
            )
            return None
        except IdentityGatewayUnavailable as exc:
            logger.warning(
                "Failed to fetch identity (%s)", type(exc).__name__, exc_info=exc
            )
            return None


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
