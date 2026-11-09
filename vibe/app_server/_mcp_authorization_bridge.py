"""Present the app server's authorization port as the one ``MCPRegistry`` reads.

The two dataclass families are structurally identical because the registry
cannot import upward, so it declares its own copies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from vibe.app_server._session_backend_port import (
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
)
from vibe.core.tools.mcp.authorization import (
    MCPAuthorizationProvider as RegistryAuthorizationPort,
    MCPAuthorizationRef as RegistryAuthorizationRef,
    MCPAuthorizationRequired as RegistryAuthorizationRequired,
    MCPAuthorizationSnapshot as RegistryAuthorizationSnapshot,
)

if TYPE_CHECKING:
    from vibe.app_server._session_backend_port import (
        MCPAuthorizationProvider,
        MCPAuthorizationResult,
    )


class RegistryAuthorizationProvider(RegistryAuthorizationPort):
    def __init__(self, provider: MCPAuthorizationProvider) -> None:
        self._provider = provider

    async def resolve(
        self, reference: RegistryAuthorizationRef
    ) -> RegistryAuthorizationSnapshot | RegistryAuthorizationRequired:
        result = await self._provider.resolve(app_authorization_ref(reference))
        return registry_authorization_result(result)

    async def reject(
        self,
        reference: RegistryAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: str,
    ) -> RegistryAuthorizationSnapshot | RegistryAuthorizationRequired:
        if reason not in {"http_unauthorized", "mcp_unauthorized"}:
            raise ValueError("Unsupported MCP authorization rejection reason")
        result = await self._provider.reject(
            app_authorization_ref(reference),
            observed_connection_revision=observed_connection_revision,
            reason=cast(Any, reason),
        )
        return registry_authorization_result(result)


def app_authorization_ref(reference: RegistryAuthorizationRef) -> MCPAuthorizationRef:
    return MCPAuthorizationRef(
        server_name=reference.server_name,
        server_fingerprint=reference.server_fingerprint,
        kind=reference.kind,
        descriptor_revision=reference.descriptor_revision,
        owner=reference.owner,
    )


def registry_authorization_ref(
    reference: MCPAuthorizationRef,
) -> RegistryAuthorizationRef:
    return RegistryAuthorizationRef(
        server_name=reference.server_name,
        server_fingerprint=reference.server_fingerprint,
        kind=reference.kind,
        descriptor_revision=reference.descriptor_revision,
        owner=reference.owner,
    )


def registry_authorization_result(
    result: MCPAuthorizationResult,
) -> RegistryAuthorizationSnapshot | RegistryAuthorizationRequired:
    if isinstance(result, MCPAuthorizationRequired):
        return RegistryAuthorizationRequired(
            reason=result.reason,
            descriptor_revision=result.descriptor_revision,
            observed_connection_revision=result.observed_connection_revision,
        )
    return RegistryAuthorizationSnapshot(
        headers=result.headers,
        connection_revision=result.connection_revision,
        descriptor_revision=result.descriptor_revision,
        expires_at=result.expires_at,
    )


__all__ = [
    "RegistryAuthorizationProvider",
    "app_authorization_ref",
    "registry_authorization_ref",
    "registry_authorization_result",
]
