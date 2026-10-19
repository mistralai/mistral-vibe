from __future__ import annotations

from vibe.app_server.protocol import MCPAddParams
from vibe.core.config import MCPHttp, MCPStaticAuth, MCPStdio, MCPStreamableHttp
from vibe.core.config.mcp_servers import (
    MCPServerAddError,
    PersistedMCPServerResult,
    persist_oauth_mcp_server,
    persist_remote_mcp_server,
    persist_stdio_mcp_server,
)
from vibe.core.config.models import normalize_mcp_server_name
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema


async def persist_mcp_add(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], params: MCPAddParams
) -> (
    PersistedMCPServerResult[MCPHttp | MCPStreamableHttp]
    | PersistedMCPServerResult[MCPStdio]
):
    if params.name is not None and not normalize_mcp_server_name(params.name):
        raise MCPServerAddError("MCP server name must contain letters or numbers.")
    timeouts = {
        name: value
        for name, value in (
            ("startup_timeout_sec", params.startup_timeout_sec),
            ("tool_timeout_sec", params.tool_timeout_sec),
        )
        if value is not None
    }
    static_requested = bool(params.headers) or any(
        value is not None
        for value in (params.api_key_env, params.api_key_header, params.api_key_format)
    )
    if params.transport == "stdio":
        if (
            params.url is not None
            or static_requested
            or params.scopes
            or params.allow_insecure_http
        ):
            raise MCPServerAddError("Remote options cannot be used with stdio.")
        if params.name is None:
            raise MCPServerAddError("name is required for stdio.")
        if params.command is None or not params.command.strip():
            raise MCPServerAddError("command is required for stdio.")
        if any(not name or "\x00" in name for name in params.env):
            raise MCPServerAddError("env must use valid environment variable names.")
        if any("\x00" in value for value in params.env.values()):
            raise MCPServerAddError("env values must not contain null bytes.")
        server = MCPStdio.model_validate({
            "name": params.name,
            "transport": "stdio",
            "command": params.command,
            "args": params.args,
            "env": params.env,
            **timeouts,
        })
        try:
            argv = server.argv()
        except ValueError as exc:
            raise MCPServerAddError(f"Invalid command: {exc}") from exc
        if not argv or not argv[0] or any("\x00" in arg for arg in argv):
            raise MCPServerAddError("command and args must form a valid command.")
        return await persist_stdio_mcp_server(orchestrator, server)

    if params.command is not None or params.args or params.env:
        raise MCPServerAddError("command, args and env can only be used with stdio.")
    if params.url is None:
        raise MCPServerAddError("url is required for remote MCP servers.")
    if static_requested and params.scopes:
        raise MCPServerAddError("OAuth scopes cannot be combined with static auth.")
    if not static_requested:
        return await persist_oauth_mcp_server(
            orchestrator,
            url=params.url,
            name=params.name,
            scopes=params.scopes,
            transport=params.transport,
            allow_insecure_http=params.allow_insecure_http,
            startup_timeout_sec=params.startup_timeout_sec,
            tool_timeout_sec=params.tool_timeout_sec,
        )
    if params.name is None:
        raise MCPServerAddError("name is required with static auth.")
    model = MCPHttp if params.transport == "http" else MCPStreamableHttp
    remote = model.model_validate({
        "name": params.name,
        "transport": params.transport,
        "url": params.url,
        "auth": _static_auth(params),
        **timeouts,
    })
    return await persist_remote_mcp_server(
        orchestrator, remote, allow_insecure_http=params.allow_insecure_http
    )


def _static_auth(params: MCPAddParams) -> MCPStaticAuth:
    if params.api_key_env == "":
        raise MCPServerAddError("apiKeyEnv must be a valid environment variable name.")
    if params.api_key_header is not None and not params.api_key_env:
        raise MCPServerAddError("apiKeyHeader requires apiKeyEnv.")
    if params.api_key_format is not None and not params.api_key_env:
        raise MCPServerAddError("apiKeyFormat requires apiKeyEnv.")
    values: dict[str, object] = {"headers": params.headers}
    for name, value in (
        ("api_key_env", params.api_key_env),
        ("api_key_header", params.api_key_header),
        ("api_key_format", params.api_key_format),
    ):
        if value is not None:
            values[name] = value
    auth = MCPStaticAuth.model_validate(values)
    if auth.api_key_env and any(
        name.lower() == auth.api_key_header.lower() for name in auth.headers
    ):
        raise MCPServerAddError(
            f"headers cannot also define API key header {auth.api_key_header!r}."
        )
    return auth
