from __future__ import annotations

from collections.abc import Awaitable, Callable

from vibe.core.auth import MCPOAuthError, delete_oauth_credentials, perform_oauth_login
from vibe.core.config import MCPHttp, MCPOAuth, MCPStreamableHttp, VibeConfigSchema
from vibe.core.config.mcp_servers import (
    MCPServerRemoveError,
    PersistedMCPServerResult,
    RemovedMCPServerResult,
    find_persisted_oauth_mcp_server,
    persist_remote_mcp_server,
    remove_mcp_server,
)
from vibe.core.config.orchestrator import ConfigOrchestrator


async def add_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    server: MCPHttp | MCPStreamableHttp,
    *,
    login: bool,
    on_oauth_url: Callable[[str], Awaitable[None]],
    on_persisted: (
        Callable[[PersistedMCPServerResult[MCPHttp | MCPStreamableHttp]], None] | None
    ) = None,
) -> PersistedMCPServerResult[MCPHttp | MCPStreamableHttp]:
    result = await persist_remote_mcp_server(orchestrator, server)
    if on_persisted is not None:
        on_persisted(result)
    if login and isinstance(result.server.auth, MCPOAuth):
        await perform_oauth_login(result.server, on_url=on_oauth_url)
    return result


async def remove_mcp_server_and_credentials(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], name: str
) -> RemovedMCPServerResult:
    # Delete credentials before the config entry: credential cleanup can fail on a
    # locked keyring, and doing it first leaves the config untouched on failure (no
    # restore needed). The concurrency-prone config write then happens last.
    server = await find_persisted_oauth_mcp_server(orchestrator, name)
    if server is not None:
        try:
            await delete_oauth_credentials(server.name)
        except MCPOAuthError as exc:
            raise MCPServerRemoveError(
                f"Failed to remove OAuth credentials for `{server.name}`: {exc}"
            ) from exc
    return await remove_mcp_server(orchestrator, name)


__all__ = ["add_mcp_server", "remove_mcp_server_and_credentials"]
